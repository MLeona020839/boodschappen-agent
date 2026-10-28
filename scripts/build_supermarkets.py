"""Write web/supermarkets.json: where the branches of the supported chains are, from OpenStreetMap.

    .venv/bin/python scripts/build_supermarkets.py

The web page uses it to find the supermarkets near an address without a slow live map query.
Branches rarely move, so running this every few months is plenty. Data © OpenStreetMap
contributors (ODbL); the page credits it.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "supermarkets.json"
QUERY = """[out:json][timeout:180];
area["ISO3166-1"="NL"][admin_level=2]->.nl;
nwr["shop"="supermarket"](area.nl);
out center tags;"""
# How each chain's branches are named in OpenStreetMap (brand, or else name).
CHAINS = {
    "ah": r"albert heijn|^ah\b", "jumbo": r"\bjumbo\b", "lidl": r"\blidl\b", "plus": r"^plus\b",
    "dirk": r"\bdirk\b", "hoogvliet": r"hoogvliet", "dekamarkt": r"dekamarkt", "spar": r"\bspar\b",
    "poiesz": r"poiesz", "vomar": r"vomar",
}


def chain(tags: dict) -> str | None:
    name = (tags.get("brand") or tags.get("name") or "").lower()
    return next((code for code, pattern in CHAINS.items() if re.search(pattern, name)), None)


def main() -> None:
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": QUERY}).encode(),
        headers={"User-Agent": "BoodschappenAgent/1.0 (+https://github.com/MLeona020839/boodschappen-agent)"},
    )
    for attempt in range(5):   # the public server answers 502/504 when it is busy
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                elements = json.load(r)["elements"]
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == 4:
                raise
            print(f"Overpass busy ({e.code}), retrying in a minute…")
            time.sleep(60)
    out: dict[str, list[list[float]]] = {code: [] for code in CHAINS}
    for el in elements:
        code = chain(el.get("tags", {}))
        lat, lon = el.get("lat", el.get("center", {}).get("lat")), el.get("lon", el.get("center", {}).get("lon"))
        if code and lat is not None:
            out[code].append([round(lat, 4), round(lon, 4)])
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    print(f"{OUT.name}: " + ", ".join(f"{c} {len(v)}" for c, v in out.items()) + f" ({OUT.stat().st_size // 1000} KB)")


if __name__ == "__main__":
    main()
