"""Value normalisation helpers shared by the pipeline and the evaluation harness."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# ISO 4217 active currency codes.
ISO_CURRENCIES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN
    BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS
    GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW
    KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD
    NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD
    SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS
    VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL
    """.split()
)

# Symbols / local spellings that map unambiguously (by documented convention) to a code.
CURRENCY_ALIASES = {
    "₹": "INR", "RS": "INR", "RS.": "INR", "RUPEES": "INR", "RUPEE": "INR", "INR.": "INR",
    "€": "EUR", "EURO": "EUR", "EUROS": "EUR",
    "£": "GBP", "POUND": "GBP", "POUNDS": "GBP",
    "US$": "USD", "$": "USD", "USD$": "USD", "DOLLAR": "USD", "DOLLARS": "USD",
    "A$": "AUD", "AU$": "AUD", "C$": "CAD", "CA$": "CAD", "S$": "SGD", "NZ$": "NZD", "HK$": "HKD",
    "¥": "JPY", "₩": "KRW", "₦": "NGN", "₱": "PHP", "฿": "THB", "₫": "VND", "₺": "TRY",
    "AED.": "AED", "DHS": "AED", "DH": "AED", "SAR.": "SAR",
}


def normalize_currency(value: object) -> str | None:
    """Return an ISO-4217 code, or None if the value is empty/unknown."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text in ISO_CURRENCIES:
        return text
    if text in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[text]
    compact = text.replace(" ", "")
    if compact in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[compact]
    return None


_NUM_CLEAN = re.compile(r"[^\d,.\-()]")


def parse_number(value: object) -> Decimal | None:
    """Parse numbers such as 3776, "3,776.00", "1,23,456.50", "3.776,00", "(12.00)", "₹ 500".

    Returns None if the value can't be parsed unambiguously.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(repr(value))
    text = str(value).strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-") and not text.startswith("-"):
        negative, text = True, text[:-1]
    text = _NUM_CLEAN.sub("", text).replace("(", "").replace(")", "")
    if text.startswith("-"):
        negative, text = True, text[1:]
    text = text.replace("-", "")
    if not text or not any(c.isdigit() for c in text):
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):  # European: 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:  # 1,234.56 / 1,23,456.78
            text = text.replace(",", "")
    elif "," in text:
        if re.fullmatch(r"\d{1,3}(,\d{2,3})+", text) and not re.fullmatch(r"\d+,\d{2}", text):
            text = text.replace(",", "")  # thousands separators (western or Indian)
        elif re.fullmatch(r"\d+,\d{1,2}", text):
            text = text.replace(",", ".")  # decimal comma
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        # 1.234.567 (European thousands) – keep only digits
        parts = text.split(".")
        if all(len(p) == 3 for p in parts[1:]):
            text = "".join(parts)
        else:
            return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return -number if negative else number


ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TEXT_DATE_FORMATS = (
    "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d-%b-%Y", "%d-%B-%Y", "%d/%b/%Y",
    "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B, %Y", "%Y/%m/%d", "%Y.%m.%d", "%d-%b-%y",
    "%d %b %y",
)


def _strip_ordinals(text: str) -> str:
    return re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)


def normalize_date(value: object, day_first: bool = True) -> tuple[str | None, bool]:
    """Normalise a date to ISO (YYYY-MM-DD).

    Returns ``(iso_or_none, ambiguous)``. ``ambiguous`` is True when a numeric date
    like 03/04/2026 could be read either way and the two readings differ.
    """
    if value is None:
        return None, False
    if isinstance(value, datetime):
        return value.date().isoformat(), False
    if isinstance(value, date):
        return value.isoformat(), False
    text = _strip_ordinals(str(value).strip()).replace("Sept", "Sep")
    if not text:
        return None, False
    if ISO_DATE_RE.match(text):
        try:
            return date.fromisoformat(text).isoformat(), False
        except ValueError:
            return None, False
    for fmt in _TEXT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat(), False
        except ValueError:
            continue
    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", text)
    if match:
        a, b, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000
        candidates = []
        for day, month in ((a, b), (b, a)) if day_first else ((b, a), (a, b)):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                continue
        if not candidates:
            return None, False
        ambiguous = len(candidates) == 2 and candidates[0] != candidates[1]
        return candidates[0].isoformat(), ambiguous
    return None, False


def normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None
