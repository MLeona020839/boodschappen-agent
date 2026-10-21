"""Import a week's promotions: read them with Claude, match them to products, save."""
from __future__ import annotations

from dataclasses import dataclass, field

import anthropic

from . import db, promos
from .agent import MODEL


@dataclass
class ImportResult:
    saved: list[dict] = field(default_factory=list)      # promotion + matched products
    unmatched: list[dict] = field(default_factory=list)  # read but no product found
    input_tokens: int = 0
    output_tokens: int = 0


def _candidates(con, store: str, query: str) -> list[dict]:
    rows = db.search(con, query, stores=[store], per_store=15)
    if not rows and len(query.split()) > 1:  # "AH scharrel kipfilet" -> try the last word
        rows = db.search(con, query.split()[-1], stores=[store], per_store=15)
    return [{"id": r["id"], "name": r["name"], "size": r["size_text"], "price": r["price"]} for r in rows]


def import_promotions(store: str, *, files: list[tuple[str, bytes]] = (), text: str = "",
                      valid_from: str | None = None, valid_to: str | None = None,
                      client: anthropic.Anthropic | None = None) -> ImportResult:
    client = client or anthropic.Anthropic()
    con, pcon = db.connect(), promos.connect()
    store_name = dict((c, n) for c, n, _ in db.stores(con)).get(store, store)
    result = ImportResult()

    extracted, usage = promos.extract(client, MODEL, store_name, files=list(files), text=text,
                                      valid_from=valid_from, valid_to=valid_to)
    _add_usage(result, usage)
    if not extracted:
        return result

    candidates = [_candidates(con, store, p.search_query) for p in extracted]
    matched, usage = promos.match(client, MODEL, extracted, candidates)
    _add_usage(result, usage)

    source = ", ".join(n for n, _ in files) or "pasted text"
    for p, ids in zip(extracted, matched):
        row = {"title": p.title, "promotion": p.label, "kind": p.kind,
               "valid_to": p.valid_to or valid_to}
        if not ids:
            result.unmatched.append(row)
            continue
        promos.add(pcon, store, p, ids, valid_from, valid_to, source)
        names = [r["name"] for r in db.get_products(con, ids).values()]
        result.saved.append({**row, "products": names})
    return result


def _add_usage(result: ImportResult, usage) -> None:
    if usage is not None:
        result.input_tokens += usage.input_tokens or 0
        result.output_tokens += usage.output_tokens or 0
