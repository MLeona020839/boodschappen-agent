"""Parse Dutch supermarket package sizes into a normalized (quantity, unit).

Units are normalized to one of: "g", "ml", "stuk".
Examples: "500 g" -> (500, "g"); "6 x 1,5 l" -> (9000, "ml"); "per stuk" -> (1, "stuk").
"""
from __future__ import annotations

import re

# unit word -> (normalized unit, multiplier)
_UNITS: dict[str, tuple[str, float]] = {
    "g": ("g", 1), "gr": ("g", 1), "grm": ("g", 1), "gram": ("g", 1), "grams": ("g", 1),
    "kg": ("g", 1000), "kilo": ("g", 1000), "kilogram": ("g", 1000), "kilos": ("g", 1000),
    "ml": ("ml", 1), "milliliter": ("ml", 1), "milliliters": ("ml", 1),
    "mililiter": ("ml", 1), "mililiters": ("ml", 1),
    "cl": ("ml", 10), "centiliter": ("ml", 10), "centiliters": ("ml", 10),
    "dl": ("ml", 100),
    "l": ("ml", 1000), "lt": ("ml", 1000), "ltr": ("ml", 1000), "liter": ("ml", 1000), "liters": ("ml", 1000),
    "st": ("stuk", 1), "stk": ("stuk", 1), "stuk": ("stuk", 1), "stuks": ("stuk", 1),
}

# Longest alternatives first so "gram" wins over "g", "liter" over "l".
_UNIT_RE = "|".join(sorted(map(re.escape, _UNITS), key=len, reverse=True))
_NUM = r"(\d+(?:\.\d+)?)"
# A unit must not be followed by another letter ("5 lagen" is not litres).
_MULTI = re.compile(rf"{_NUM}\s*x\s*{_NUM}\s*({_UNIT_RE})(?![a-z])")
_SINGLE = re.compile(rf"{_NUM}\s*({_UNIT_RE})(?![a-z])")
_PER_UNIT = re.compile(rf"^per\s+({_UNIT_RE})$")
# "per pak", "per bos", ... : sold as one piece
_PER_PIECE = re.compile(r"^per\s+(pak|pakket|bos|paar|zak|doos|bak|net|tros|schaal)$")


def _clean(text: str) -> str:
    text = text.lower().strip()
    # Dutch decimal comma; avoid touching thousands separators like "1.000" (rare in sizes).
    text = re.sub(r"(\d),(\d)", r"\1.\2", text)
    text = text.replace("×", "x")
    text = re.sub(r"\bca\.?\s*(?=\d)", "", text)  # "ca. 500 g" (but leave "cacao" alone)
    return text


def _from_match(qty: float, unit_word: str) -> tuple[float, str]:
    unit, mult = _UNITS[unit_word]
    return round(qty * mult, 3), unit


def parse_size(text: str | None) -> tuple[float, str] | None:
    """Parse a size string such as '500 g', 'per 1000 ml', '6 x 330 ml', 'per stuk'."""
    if not text:
        return None
    t = _clean(text)
    if m := _PER_UNIT.match(t):  # "per stuk", "per kilo"
        return _from_match(1, m.group(1))
    if _PER_PIECE.match(t):
        return 1, "stuk"
    if m := _MULTI.search(t):
        return _from_match(float(m.group(1)) * float(m.group(2)), m.group(3))
    if m := _SINGLE.search(t):
        qty = float(m.group(1))
        if qty > 0:
            return _from_match(qty, m.group(2))
    return None


def parse_size_from_name(name: str) -> tuple[float, str] | None:
    """Fallback for stores (Jumbo, Vomar) that put the size in the product name."""
    t = _clean(name)
    if m := _MULTI.search(t):
        return _from_match(float(m.group(1)) * float(m.group(2)), m.group(3))
    matches = list(_SINGLE.finditer(t))
    if matches:  # the size is usually at the end of the name
        m = matches[-1]
        if float(m.group(1)) > 0:
            return _from_match(float(m.group(1)), m.group(2))
    return None


def unit_price_label(unit: str) -> str:
    return {"g": "€/kg", "ml": "€/L", "stuk": "€/st"}[unit]


def unit_price(price: float, qty: float, unit: str) -> float:
    """Price per kg, per litre, or per piece."""
    per = 1000 if unit in ("g", "ml") else 1
    return round(price / qty * per, 2)
