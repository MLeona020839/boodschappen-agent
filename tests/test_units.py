import pytest

from grocery_agent.units import parse_size, parse_size_from_name, unit_price


@pytest.mark.parametrize("text,expected", [
    ("500 g", (500, "g")),
    ("ca. 400 g", (400, "g")),
    ("1,5 l", (1500, "ml")),
    ("0,75 l", (750, "ml")),
    ("Per 1000 ml", (1000, "ml")),
    ("6 x 330 ml", (1980, "ml")),
    ("4 x 1,5 l • zonder doos", (6000, "ml")),
    ("2 KG", (2000, "g")),
    ("750 milliliter", (750, "ml")),
    ("200 Milliliter", (200, "ml")),
    ("145.6 Gram", (145.6, "g")),
    ("33 cl", (330, "ml")),
    ("6 Stuks", (6, "stuk")),
    ("per stuk", (1, "stuk")),
    ("per kilo", (1000, "g")),
    ("per pakket", (1, "stuk")),
    ("1 kg (ca. 8 stuks)", (1000, "g")),
    ("330ML", (330, "ml")),
    ("125gr", (125, "g")),
])
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", None, "4 pers | 25 min", "20 wasbeurten", "5 lagen", "0 g"])
def test_parse_size_unknown(text):
    assert parse_size(text) is None


@pytest.mark.parametrize("name,expected", [
    ("Jumbo Kipfilet 800 g", (800, "g")),
    ("Texels Skuumkoppe 0.0%  6x330ML", (1980, "ml")),
    ("Mora Kroketten 4 x 75 g", (300, "g")),
    ("Cacao poeder 250 g", (250, "g")),
    ("Halfvolle Melk 1,5 L", (1500, "ml")),
])
def test_parse_size_from_name(name, expected):
    assert parse_size_from_name(name) == expected


def test_parse_size_from_name_none():
    assert parse_size_from_name("Fruitsalade") is None


def test_unit_price():
    assert unit_price(2.49, 500, "g") == 4.98   # €/kg
    assert unit_price(1.79, 1500, "ml") == 1.19  # €/L
    assert unit_price(3.00, 6, "stuk") == 0.5    # €/st
