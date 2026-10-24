"""Disclosed ticker string → price-vendor symbol.

PTR text is typed by congressional staff and parsed out of PDFs, so the
`transactions.ticker` column holds a mix of real symbols, vendor-specific
spellings, spin-off artifacts and outright noise. This module is the one
place that decides what a disclosed string means.

Two things to keep straight:

* **Resolution is non-destructive.** `transactions.ticker` keeps whatever was
  disclosed; only the *fetch* uses the resolved symbol, and bars are stored
  back under the disclosed ticker. Every existing join and view keeps working
  and nothing re-labels historical data.

* **It never maps one company onto another.** Dots become dashes because
  that's the same security under a different vendor convention. An acquired
  or renamed company is NOT mapped to its successor: pointing ATVI at MSFT
  (or TPX at its post-rename symbol) would splice two different price series
  together across a corporate action and silently fabricate returns. Those
  are reported as unresolved and left for a curated, sourced mapping.
"""
from __future__ import annotations

import re

# Strings that reach the ticker column but are not tickers: parser pickups of
# column headings, exchange names, asset-class words and company names.
NOT_TICKERS = {
    "APPLE", "ETF", "ETN", "FUND", "FUNDS", "STOCK", "STOCKS", "SHARES",
    "NYSE", "NASDAQ", "AMEX", "ARCA", "OTC", "BOND", "BONDS", "CASH",
    "NONE", "N/A", "NA", "TBD", "VARIOUS", "COMMON", "CLASS", "INC",
    "CORP", "LLC", "LP", "TRUST", "UNKNOWN", "SP", "JT", "DC",
}

# A plain US listing, optionally with a share-class suffix (BRK.B / BRK-B).
# Only A/B/C count as classes: the other single-letter suffixes that turn up
# here are venue or status markers, not share classes — .V is TSX Venture,
# .W is when-issued, .Z is a vendor status flag — and Yahoo serves none of
# them on the US tape, so rewriting them would invent a symbol.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_CLASS_RE = re.compile(r"^([A-Z]{1,5})[.\-]([ABC])$")


def resolve(raw: str | None) -> str | None:
    """Return the symbol to fetch prices under, or None if `raw` isn't a
    usable US-listed ticker.

    None means "don't try to price this", not "this trade is invalid" — the
    transaction still exists and still counts in the disclosure record.
    """
    if not raw:
        return None
    s = raw.strip().upper()
    if not s:
        return None

    # Multi-token cells come in two shapes, and the first *plausible* token
    # is right for both. Spin-off / distribution rows name the parent then
    # the new entity ("T WBD", "JNJ KVUE", "GE GEHC") — the disclosed holding
    # is the parent. Other rows carry a "--" placeholder where the parser
    # found no parent ("-- RTX"), and the real symbol trails it.
    if " " in s:
        tokens = [t for t in s.split()
                  if _TICKER_RE.match(t) or _CLASS_RE.match(t)]
        if not tokens:
            return None
        s = tokens[0]

    if s in NOT_TICKERS:
        return None

    # Class shares: Yahoo spells BRK.B as BRK-B. Same security, different
    # vendor convention — safe to rewrite.
    m = _CLASS_RE.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # Anything still carrying a dot is a non-US venue suffix (FL4.SG, VIV.V,
    # WY.Z) that Yahoo won't serve on the US tape.
    if "." in s:
        return None

    if _TICKER_RE.match(s):
        return s
    return None


def is_rewritten(raw: str | None) -> bool:
    """True when the fetch symbol differs from the disclosed string."""
    r = resolve(raw)
    return r is not None and r != (raw or "").strip().upper()
