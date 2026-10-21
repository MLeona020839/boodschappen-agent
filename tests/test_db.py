import json

from grocery_agent import db


RAW = [
    {"n": "ah", "c": "AH", "u": "https://ah/", "d": [
        {"n": "AH Halfvolle melk", "l": "wi1/melk", "p": 1.29, "s": "1 l"},
        {"n": "Chocolademelk", "l": "wi2/choco", "p": 1.99, "s": "1 l"},
        {"n": "AH Kipfilet", "l": "wi3/kip", "p": 5.99, "s": "ca. 400 g"},
        {"n": "AH Kipfilet", "l": "wi3/kip", "p": 5.99, "s": "ca. 400 g"},  # duplicate row
        {"n": "[X] Emballage", "l": "wi4", "p": 0.15, "s": ""},             # junk row
    ]},
    {"n": "jumbo", "c": "Jumbo", "u": "https://jumbo/", "d": [
        {"n": "Jumbo Kipfilet 800 g", "l": "kip-800", "p": 9.59, "s": ""},
    ]},
]


def make_db(tmp_path, raw=RAW):
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(raw))
    info = db.build(raw_path, tmp_path / "p.db")
    return info, db.connect(tmp_path / "p.db")


def test_build_counts_and_filters(tmp_path):
    info, con = make_db(tmp_path)
    assert info["counts"] == {"ah": 3, "jumbo": 1}


def test_size_parsed_from_name(tmp_path):
    _, con = make_db(tmp_path)
    row = con.execute("SELECT qty, unit, unit_price FROM products WHERE store='jumbo'").fetchone()
    assert tuple(row) == (800, "g", 11.99)


def test_search_substring_and_ranking(tmp_path):
    _, con = make_db(tmp_path)
    names = [r["name"] for r in db.search(con, "melk")]
    assert names == ["AH Halfvolle melk", "Chocolademelk"]  # whole word first
    assert {r["store"] for r in db.search(con, "filet")} == {"ah", "jumbo"}
    assert [r["store"] for r in db.search(con, "kip", stores=["jumbo"])] == ["jumbo"]


def test_ids_stable_across_rebuilds(tmp_path):
    _, con = make_db(tmp_path)
    ids1 = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM products")}
    con.close()
    raw2 = json.loads(json.dumps(RAW))
    raw2[0]["d"].insert(0, {"n": "Nieuw product", "l": "wi9", "p": 1.0, "s": "1 stuk"})
    raw2[0]["d"][1]["p"] = 1.19  # price change keeps the id
    _, con = make_db(tmp_path, raw2)
    ids2 = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM products")}
    assert all(ids2[n] == i for n, i in ids1.items())
