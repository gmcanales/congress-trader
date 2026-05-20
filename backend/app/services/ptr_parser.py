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


# ── Public API ──────────────────────────────────────────────────────────────

def parse_ptr_text(text: str, source: str = "digital") -> ParsedPTR:
    """Parse already-extracted PTR text. `source` is 'digital' or 'ocr'."""
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
