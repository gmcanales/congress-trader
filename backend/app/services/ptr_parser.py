"""
Parser for House Periodic Transaction Report (PTR) PDFs.

The House Clerk publishes individual PTR filings only as PDFs. Digital eFD
filings (DocID starting with 20...) carry a text layer; paper filings
(DocID starting with 8...) are scanned images and require OCR.

This module extracts text via `pdftotext -layout` (poppler) and parses the
transaction table with layout-tolerant heuristics. Each transaction is given
a confidence rating because the PDF layout wraps asset names and amounts
across lines unpredictably.
"""
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum extracted characters before we consider a PDF "digital" (vs scanned)
MIN_TEXT_LAYER_CHARS = 200

# A transaction's anchor row: type letter + two dates + an amount.
ANCHOR_RE = re.compile(
    r"\b(?P<type>S \(partial\)|[PSE])\s+"
    r"(?P<txdate>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<notif>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<amt>\$[\d,].*?)\s*$"
)

# Sub-records that follow a transaction (Filing Status, Subholding Owner, Description)
SUBRECORD_RE = re.compile(r"^(F\s+S\b|S\s+O\s*:|D\s+:|C\s+:|L\s+:)")

# Ticker immediately preceding an asset-type bracket, e.g. "(AAPL) [ST]"
TICKER_NEAR_TYPE_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*\[([A-Z]{2})\]")
# Fallback: any parenthesised all-caps token
TICKER_LOOSE_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")
ASSET_TYPE_RE = re.compile(r"\[([A-Z]{2})\]")

TYPE_MAP = {
    "P": "purchase",
    "S": "sale",
    "S (partial)": "sale_partial",
    "E": "exchange",
}

# Asset types that are expected to have a stock ticker
TICKERED_ASSET_TYPES = {"ST", "ET", "AB", "OP"}


@dataclass
class ParsedTransaction:
    owner: Optional[str]
    asset_name: str
    ticker: Optional[str]
    asset_type: Optional[str]
    transaction_type: str
    transaction_date: Optional[date]
    notification_date: Optional[date]
    amount_min: Optional[float]
    amount_max: Optional[float]
    amount_raw: str
    confidence: str  # high | medium | low
    raw_text: str


@dataclass
class ParsedPTR:
    filing_id: Optional[str] = None
    member_name: Optional[str] = None
    state_dst: Optional[str] = None
    parse_method: str = "digital"  # digital | ocr | failed
    transactions: list = field(default_factory=list)


# ── Text extraction ─────────────────────────────────────────────────────────

def extract_text_digital(pdf_path: str) -> str:
    """Extract a layout-preserved text layer using poppler's pdftotext."""
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True,
            timeout=60,
        )
        return out.stdout.decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        logger.warning(f"pdftotext failed for {pdf_path}: {exc}")
        return ""


def has_text_layer(text: str) -> bool:
    return len(text.strip()) >= MIN_TEXT_LAYER_CHARS


# ── Field helpers ───────────────────────────────────────────────────────────

def _parse_date(raw: str) -> Optional[date]:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> tuple[Optional[float], Optional[float]]:
    """Parse an amount-range string like '$1,001 - $15,000' or 'Over $5,000,000'."""
    raw = (raw or "").strip()
    nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+", raw) if n.strip(",")]
    if not nums:
        return None, None
    if re.search(r"[Oo]ver", raw) or raw.rstrip().endswith("-"):
        return nums[0], (nums[1] if len(nums) > 1 else None)
    if len(nums) >= 2:
        return nums[0], nums[1]
    return nums[0], nums[0]


def _extract_ticker(asset_text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (ticker, asset_type). Prefer the ticker glued to an asset-type bracket."""
    m = TICKER_NEAR_TYPE_RE.search(asset_text)
    if m:
        return m.group(1), m.group(2)
    type_m = ASSET_TYPE_RE.search(asset_text)
    asset_type = type_m.group(1) if type_m else None
    loose = TICKER_LOOSE_RE.findall(asset_text)
    return (loose[-1] if loose else None), asset_type


# ── Transaction table parsing ───────────────────────────────────────────────

def _slice_table(text: str) -> list[str]:
    """Return the lines between the transaction-table header and footer."""
    lines = text.splitlines()
    start, end = 0, len(lines)
    for i, line in enumerate(lines):
        if re.match(r"\s*ID\b.*Owner.*Asset", line):
            start = i + 1
        elif "complete list of asset type" in line:
            end = i
            break
    return lines[start:end]


def _parse_table(table_lines: list[str], base_confidence: str) -> list[ParsedTransaction]:
    anchors = [(i, m) for i, line in enumerate(table_lines)
               if (m := ANCHOR_RE.search(line))]

    transactions: list[ParsedTransaction] = []
    for idx, (line_i, m) in enumerate(anchors):
        flags: list[str] = []
        raw_lines = [table_lines[line_i]]

        pre = table_lines[line_i][:m.start()].strip()
        owner = None
        owner_m = re.match(r"^(SP|JT|DC)\b", pre)
        if owner_m:
            owner = owner_m.group(1)
            pre = pre[owner_m.end():].strip()

        asset_parts = [pre] if pre else []
        amount_raw = m.group("amt").strip()
        amount_wrapped = amount_raw.endswith("-")

        end_i = anchors[idx + 1][0] if idx + 1 < len(anchors) else len(table_lines)
        for j in range(line_i + 1, end_i):
            line = table_lines[j].strip()
            if not line:
                continue
            if SUBRECORD_RE.match(line):
                break
            # The wrapped second half of an amount may sit alone on a line, or
            # share a line with wrapped asset text (e.g. "[CS]   $50,000").
            if amount_wrapped:
                cont = re.search(r"\$[\d,]+", line)
                if cont:
                    amount_raw = f"{amount_raw} {cont.group(0)}"
                    amount_wrapped = False
                    flags.append("amount_wrapped")
                    raw_lines.append(table_lines[j])
                    remainder = (line[:cont.start()] + line[cont.end():]).strip()
                    if remainder:
                        asset_parts.append(remainder)
                    continue
            asset_parts.append(line)
            raw_lines.append(table_lines[j])

        asset_name = re.sub(r"\s+", " ", " ".join(asset_parts)).strip()
        ticker, asset_type = _extract_ticker(asset_name)
        tx_type = TYPE_MAP.get(m.group("type"), "unknown")
        amount_min, amount_max = _parse_amount(amount_raw)

        # Confidence scoring. A *resolved* amount wrap is a deterministic join
        # and is the common case in these PDFs, so it does not lower confidence;
        # only an *unresolved* wrap (continuation never found) does.
        confidence = base_confidence
        if amount_wrapped:
            flags.append("amount_unresolved")
            confidence = "low"
        if asset_type in TICKERED_ASSET_TYPES and not ticker:
            flags.append("expected_ticker_missing")
            confidence = "medium" if confidence == "high" else confidence
        if len(asset_parts) > 4:
            flags.append("asset_spans_many_lines")
            confidence = "medium" if confidence == "high" else confidence
        if tx_type == "unknown":
            confidence = "low"

        transactions.append(ParsedTransaction(
            owner=owner,
            asset_name=asset_name,
            ticker=ticker,
            asset_type=asset_type,
            transaction_type=tx_type,
            transaction_date=_parse_date(m.group("txdate")),
            notification_date=_parse_date(m.group("notif")),
            amount_min=amount_min,
            amount_max=amount_max,
            amount_raw=amount_raw,
            confidence=confidence,
            raw_text="\n".join(raw_lines),
        ))

    return transactions


# ── OGE Form 278-T parsing (cabinet / executive branch, 2026-05-23) ─────────
# 278-T tables differ from House PTR tables on every column:
#   House:  ID | Owner | Asset | Type-letter | Date | Notif | Amount
#   278-T:  # | Description | Type-word | Date | Notif-flag | Amount
# Key differences:
#   - No owner column (cabinet officials file individually; no SP/JT/DC)
#   - No ticker column (description is raw company / asset name; resolution
#     happens downstream)
#   - Transaction types spelled out ("Purchase" / "Sale (Full)" / etc.)
#   - Notification is a YES/NO flag for "over 30 days", not a date
# Layout is wide and field-positions vary, so we anchor on the right-side
# columns (type word + date + amount) and recover the description from the
# left.

ANCHOR_RE_278T = re.compile(
    r"^\s+(?P<desc>\S.*?)\s{2,}"
    r"(?P<type>Purchase|Sale\s*\([A-Za-z]+\)|Sale|Exchange)\s+"
    r"(?P<txdate>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<amt>(?:\$|Over\s*\$)[\d,. \-•$]+)\s*$"
)

TYPE_MAP_278T = {
    "Purchase":         "purchase",
    "Sale":             "sale",
    "Sale (Full)":      "sale",
    "Sale (Partial)":   "sale_partial",
    "Sale (partial)":   "sale_partial",
    "Exchange":         "exchange",
}


def _is_278t(text: str) -> bool:
    """Detect whether this PDF is an OGE Form 278-T (executive branch) vs
    a House PTR. The form name appears in the header of every page."""
    return "OGE Form 278-T" in text or "OGE Fonn 278-T" in text  # poppler typo


def _parse_amount_278t(raw: str) -> tuple[Optional[float], Optional[float]]:
    """Same brackets as House PTR. Tolerates the en-dash / bullet glyphs and
    space-inside-number artifacts ("$1 ,000,000" → "$1,000,000") that
    poppler's pdftotext frequently emits on 278-T form layouts."""
    raw = re.sub(r"[•–—]", "-", raw or "")
    # Strip space inserted between a digit and a comma (the most common artifact)
    raw = re.sub(r"(\d)\s+(,)", r"\1\2", raw)
    # And between a comma and a digit
    raw = re.sub(r"(,)\s+(\d)", r"\1\2", raw)
    raw = raw.replace("$ ", "$").strip()
    return _parse_amount(raw)


def parse_278t_text(text: str, source: str = "digital") -> ParsedPTR:
    """Parse an OGE 278-T PDF's already-extracted text. `source` is
    'digital' or 'ocr'. Returns a ParsedPTR where:
      - `transactions[].owner` is always None (278-T has no owner column)
      - `transactions[].ticker` is always None (description-only; resolve
        to a ticker downstream)
      - `transactions[].notification_date` is always None (278-T uses a
        boolean flag, not a date)
      - `asset_name` is the full description string verbatim
    """
    result = ParsedPTR(parse_method=source)

    # Filer name lives in the form-header table near the top of the PDF.
    # Layout (column headers on one line, values on the next):
    #   "Last Name  [whitespace] First Name [whitespace] Mi"
    #   "Trump      [whitespace] Donald"
    # poppler sometimes collapses the trailing "Mi" header label into the
    # preceding "First Name" cell — be lenient.
    header_m = re.search(
        r"Last Name\s+First Name(?:\s+M[il]?)?\s*\n+(\S[^\n]*?)\s{3,}(\S[^\n]*?)(?:\s+|$)",
        text,
    )
    if header_m:
        last = header_m.group(1).strip()
        first = header_m.group(2).strip()
        result.member_name = f"{first} {last}".strip()
    else:
        # Fall back to "Filer's Name" block which appears further down on the
        # cover page. Skip any "Page N of M" garbage that poppler may insert.
        for m in re.finditer(r"Filer'?s? Name\s*\n+\s*(\S[^\n]*)", text):
            cand = m.group(1).strip()
            if not re.search(r"\b[Pp]a[gq]e\b|\bof\b", cand):
                result.member_name = cand
                break

    base_confidence = "low" if source == "ocr" else "medium"
    # Less than 'high' because no ticker is extractable from the description
    # and downstream resolution will add uncertainty.

    transactions: list[ParsedTransaction] = []
    for line in text.splitlines():
        m = ANCHOR_RE_278T.match(line)
        if not m:
            continue
        type_raw = re.sub(r"\s+", " ", m.group("type")).strip()
        tx_type = TYPE_MAP_278T.get(type_raw, "unknown")
        amount_min, amount_max = _parse_amount_278t(m.group("amt"))
        confidence = base_confidence
        if tx_type == "unknown":
            confidence = "low"
        if amount_min is None:
            confidence = "low"

        transactions.append(ParsedTransaction(
            owner=None,
            asset_name=m.group("desc").strip(),
            ticker=None,
            asset_type=None,
            transaction_type=tx_type,
            transaction_date=_parse_date(m.group("txdate")),
            notification_date=None,
            amount_min=amount_min,
            amount_max=amount_max,
            amount_raw=m.group("amt").strip(),
            confidence=confidence,
            raw_text=line,
        ))

    result.transactions = transactions
    return result


# ── Public API ──────────────────────────────────────────────────────────────

def parse_ptr_text(text: str, source: str = "digital") -> ParsedPTR:
    """Parse already-extracted PTR text. `source` is 'digital' or 'ocr'.
    Auto-detects OGE Form 278-T (executive branch) vs House PTR and
    dispatches to the appropriate parser."""
    if _is_278t(text):
        return parse_278t_text(text, source=source)

    result = ParsedPTR(parse_method=source)

    fid = re.search(r"Filing ID\s*#?\s*(\d+)", text)
    if fid:
        result.filing_id = fid.group(1)
    name = re.search(r"Name:\s*(.+)", text)
    if name:
        result.member_name = name.group(1).strip()
    sd = re.search(r"State/District:\s*([A-Z]{2}\d{2})", text)
    if sd:
        result.state_dst = sd.group(1)

    base_confidence = "low" if source == "ocr" else "high"
    result.transactions = _parse_table(_slice_table(text), base_confidence)
    return result
