from __future__ import annotations

import re
from pathlib import Path

_LANG_CODES = {
    "en", "fr", "de", "es", "it", "pt", "nl", "sv", "no", "fi", "da",
}

_STOPWORDS = {
    "report", "reports", "sustainability", "climate", "progress", "update",
    "statement", "overview", "esg", "rse", "csr", "csrd", "esrs",
    "nonfinancial", "non-financial", "annual", "integrated",
}


def clean_company_name(raw: str) -> str:
    """
    Produce a clean display name from a filename-like string.
    Strips year / language suffixes and normalizes casing.
    """
    if not raw:
        return "Company"

    base = Path(raw).stem
    base = re.sub(r"[_\\-]+", " ", base).strip()
    tokens = [t for t in base.split() if t]

    def is_year(tok: str) -> bool:
        return tok.isdigit() and len(tok) == 4 and tok.startswith(("19", "20"))

    def is_lang(tok: str) -> bool:
        return tok.lower() in _LANG_CODES

    def is_stop(tok: str) -> bool:
        return tok.lower() in _STOPWORDS

    while tokens and (is_year(tokens[-1]) or is_lang(tokens[-1]) or is_stop(tokens[-1])):
        tokens.pop()

    if not tokens:
        tokens = [t for t in base.split() if t]

    def norm(tok: str) -> str:
        if any(ch.isdigit() for ch in tok):
            return tok
        if tok.isupper() and len(tok) <= 4:
            return tok
        if tok.islower() or tok.istitle():
            return tok.capitalize()
        return tok

    cleaned = " ".join(norm(t) for t in tokens).strip()
    return cleaned or base or "Company"
