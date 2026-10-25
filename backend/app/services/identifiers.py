"""Product identifier normalisation (GTIN/EAN/UPC/ISBN) and title normalisation."""

import re
import unicodedata

_NON_DIGIT = re.compile(r"\D")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def gtin_check_digit(body: str) -> int:
    """Check digit for a GTIN body (all digits except the last)."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def normalise_gtin(value: str | None) -> str | None:
    """Return a valid GTIN-14 string (zero-padded) or ``None``.

    UPC-A (12), EAN-8, EAN-13 and GTIN-14 all normalise to the same 14-digit form,
    so the same product listed with a UPC on one marketplace and an EAN on another
    still matches exactly. Invalid check digits are rejected rather than trusted.
    """
    if not value:
        return None
    digits = _NON_DIGIT.sub("", str(value))
    if len(digits) not in (8, 12, 13, 14):
        return None
    if set(digits) == {"0"}:
        return None
    if gtin_check_digit(digits[:-1]) != int(digits[-1]):
        return None
    return digits.zfill(14)


def make_gtin13(body12: str) -> str:
    body12 = _NON_DIGIT.sub("", body12)[:12].zfill(12)
    return body12 + str(gtin_check_digit(body12))


_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "for",
    "with",
    "of",
    "in",
    "on",
    "new",
    "pack",
    "set",
    "x",
    "uk",
    "us",
    "free",
    "delivery",
    "shipping",
    "sale",
    "hot",
    "best",
    "2024",
    "2025",
    "2026",
}


def normalise_title(title: str | None) -> str:
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\[demo\]", " ", text)
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def title_tokens(title: str | None) -> set[str]:
    return {t for t in normalise_title(title).split() if t not in _STOPWORDS and len(t) > 1}


def normalise_model(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[\s\-_/.]", "", value).upper()
    return cleaned or None
