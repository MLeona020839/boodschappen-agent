"""Import a week's promotions: read them with Claude, match them to products, save."""
from __future__ import annotations

from dataclasses import dataclass, field

import anthropic

from . import db, promos
from .agent import MODEL


MAX_CANDIDATES = 25


@dataclass
class ImportResult:
    saved: list[dict] = field(default_factory=list)      # promotion + matched products
    unmatched: list[dict] = field(default_factory=list)  # read but no product found
    store: str = ""                                      # store the promotions were saved under
    switched_from: str | None = None                     # chosen store, if the folder showed another
    input_tokens: int = 0
    output_tokens: int = 0


def _singular(word: str) -> str:
    """Rough Dutch singular: 'schnitzels' -> 'schnitzel', "paprika's" -> 'paprika', 'uien' stays."""
    for suffix in ("'s", "’s", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def search_queries(promo) -> list[str]:
    """Search terms to try, most specific first, without duplicates."""
    terms = [promo.search_query, *getattr(promo, "search_terms", [])]
    out: list[str] = []
    for term in terms:
        term = " ".join(term.lower().replace(",", " ").split())
        if not term:
            continue
        singular = " ".join(_singular(w) for w in term.split())
        # Folders write "Capri-Sun", catalogues "Capri Sun" (and the other way round).
        for q in (term, singular, singular.replace("-", " ")):
            if q not in out:
                out.append(q)
    return out


def _candidates(con, store: str, promo) -> list[dict]:
    """Products in `store` that might be what the promotion is about, from several searches."""
    queries = search_queries(promo)
    results = [db.search(con, q, stores=[store], per_store=MAX_CANDIDATES) for q in queries]
    if not any(results):  # nothing for whole phrases: try the individual longer words
        words = dict.fromkeys(w for q in queries for w in q.split() if len(w) >= 4)
        results = [db.search(con, w, stores=[store], per_store=10) for w in words]

    # Take turns between searches, so 'coca-cola' can't fill every slot before 'fanta'.
    rows, seen = [], set()
    for rank in range(MAX_CANDIDATES):
        for found in results:
            if rank < len(found) and found[rank]["id"] not in seen and len(rows) < MAX_CANDIDATES:
                seen.add(found[rank]["id"])
                rows.append(found[rank])
    return [{"id": r["id"], "name": r["name"], "size": r["size_text"], "price": r["price"]} for r in rows]


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


ALIASES = {"ah": ["albertheijn"], "plus": ["plussupermarkt"], "dekamarkt": ["deka"]}


def store_from_folder(seen: str | None, stores: list[tuple[str, str, int]]) -> str | None:
    """Store code whose name appears in the name printed on the folder, or None if unsure."""
    if not seen:
        return None
    seen_n = _norm(seen)
    hits = [code for code, name, _ in stores
            if any(n and n in seen_n for n in (_norm(code), _norm(name.split("(")[0]), *ALIASES.get(code, [])))]
    return hits[0] if len(hits) == 1 else None


def import_promotions(store: str, *, files: list[tuple[str, bytes]] = (), text: str = "",
                      valid_from: str | None = None, valid_to: str | None = None,
                      client: anthropic.Anthropic | None = None) -> ImportResult:
    client = client or anthropic.Anthropic()
    con, pcon = db.connect(), promos.connect()
    stores = db.stores(con)
    store_name = dict((c, n) for c, n, _ in stores).get(store, store)
    result = ImportResult(store=store)

    parsed, usage = promos.extract(client, MODEL, store_name, files=list(files), text=text,
                                   valid_from=valid_from, valid_to=valid_to)
    _add_usage(result, usage)
    # A Dirk folder imported with "AH" still selected would be matched against the wrong shop.
    seen = store_from_folder(parsed.store_on_folder, stores)
    if seen and seen != store:
        # Dates left at the chosen store's default week follow the folder's store instead.
        default = tuple(d.isoformat() for d in promos.promo_week(store))
        if (valid_from, valid_to) == default:
            valid_from, valid_to = (d.isoformat() for d in promos.promo_week(seen))
        result.switched_from, result.store, store = store, seen, seen
    extracted = parsed.promotions
    if not extracted:
        return result

    candidates = [_candidates(con, store, p) for p in extracted]
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
