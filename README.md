# Boodschappen Agent: a grocery agent for Dutch supermarkets

Plan the week's meals in plain language, and the agent works out where to buy everything
cheapest across 10 Dutch supermarkets (AH, Jumbo, Lidl, Plus, Dirk and more, about 107k products).

> *"Two people, four dinners this week, €35 budget, something Asian, no coriander."*
> → a meal plan, a shopping list grouped by store, the total, and whether a second store is worth the trip.

**Live demo: [boodschappen-agent-ming.streamlit.app](https://boodschappen-agent-ming.streamlit.app/)** (bring your own Anthropic API key) · [中文说明](README.zh.md)

![Planning four dinners for two around this week's AH promotions](docs/screenshot.webp)

## What it does

- **Natural-language meal planning and price comparison** in any language. The product data is
  Dutch; the agent searches in Dutch on your behalf.
- **Exact basket pricing**: the cheapest mix of pack sizes per store (800 g + 175 g beats
  2 × 800 g), the best single store, and the best two-store split with the savings.
- **Weekly promotions**: drop in a folder screenshot, photo or PDF, or paste the text of a bonus page.
  Claude reads the deals, matches them to products, and pricing then applies
  1+1 gratis, "2 voor €5", 25% korting, 2e halve prijs and plain price cuts.
- **Remembers preferences**: household size, dislikes, favourite stores.
- **Free to try, with a hard cost ceiling**: open to everyone on the owner's key within a shared daily budget (e.g. €3, resetting at midnight Amsterdam time) and a per-session cap, tracked in Postgres; only the owner can import or delete promotions.
- **English, Dutch or Chinese UI** (switch top right, or link to `?lang=Nederlands` / `?lang=中文`); the agent replies in whatever language you write.
- **Chat on the left, tools on the right**: drop screenshots from the bottom-right corner of your screen straight onto the promotion upload box.

## How it works

```
Checkjebon open price data (daily, 10 stores)
   │  db.py: download → parse pack sizes → SQLite + FTS5 trigram index, stable product ids
   ▼
Tools (tools.py)                         Promotions (promos.py, importer.py)
  search_products                          image / PDF / text
  price_shopping_list ──► basket.py          → Claude structured output (extract)
  list_promotions          knapsack DP       → candidate search → Claude picks products
  remember / forget_preference               → SQLite or Postgres (SQLAlchemy)
   ▼
agent.py: Claude (claude-opus-5-5) tool-use loop, multi-turn history, optional token streaming
   ▼
api/main.py: FastAPI — streams each turn as server-sent events (tool steps + text), daily and
             per-session budgets (quota.py), per-IP rate limits, owner-only promotion endpoints
   ▼
web/: static page in plain HTML/CSS/JS (no build step), served behind Caddy; loads instantly

app.py: the earlier Streamlit UI on the same modules, still runnable
```

**Division of labour.** The LLM does what it is good at: understanding the request, designing
meals, and deciding which products are "the same thing" across stores (entity matching).
All arithmetic (pack counts, totals, store comparisons, promotion maths) is deterministic code
the model calls as a tool. The model never adds up prices itself.

## Interesting problems

| Problem | Approach |
|---|---|
| Pack sizes come in dozens of formats: `ca. 400 g`, `6 x 330 ml`, `per stuk`, `1,5 L`, or only in the product name | Regex + unit table normalising to g / ml / pieces → €/kg, €/L. 83% of products parsed; the rest are priced as "one pack" and flagged |
| Dutch compound words: `filet` should find `kipfilet` | SQLite FTS5 **trigram** index (substring search); ranking prefers whole-word matches and shorter, plain product names |
| Ids referenced in a conversation must survive the daily data refresh | Product id = hash of store, link, name and size, so it stays the same across rebuilds |
| Buy ≥ 900 g with 800 g and 175 g packs available | **Unbounded covering knapsack** (DP over grams) per store and ingredient |
| 1+1 and "2 voor €5" make cost non-linear in quantity | Each deal becomes an extra *bundle option* in the same DP; on a cost tie, the bundle wins (more food for the same money) |
| One store or two? | Enumerate all single stores and store pairs; report the pair only if it actually uses both stores and saves money |
| Promotions must outlive price refreshes and cloud restarts | Separate store (SQLAlchemy: SQLite locally, Postgres such as Supabase when deployed), linked by stable product ids |
| LLM-chosen product ids could be invented | Matching output is filtered against the candidate set the model was shown |
| Store-filtered search took 0.5–3 s: SQLite scanned the store's rows and ran `MATCH` on each | Materialise the full-text hits first, then filter by store: about 1 ms, roughly 1000× faster |
| Folder wording rarely equals catalogue wording ("Varkensschnitzels", "Capri-Sun", "A, B of C") | Claude gives several search terms; singular and de-hyphenated variants; searches take turns filling the candidate list. On a 36-item Dirk folder, matched promotions went from 20 to 28 |
| A Dirk folder imported with "AH" selected | No default store, and the store printed on the folder (read by Claude) overrides a wrong choice |

## Testing

128 tests (`pytest -q`):

- unit-size parsing, knapsack and promotion pricing, single/two-store comparison
- database build, search ranking, and id stability across refreshes
- promotion storage on SQLite and on **real Postgres** (via `pgserver`, optional)
- the agent tool loop against a **mocked Messages API**, both regular and **streamed (SSE)** responses
- the FastAPI backend: event stream order, cost recorded for failed turns, daily/session budgets, per-IP
  rate limiting, one question at a time per session, signed and expiring owner tokens, import uploads
- the Streamlit UI headless with `AppTest` (open access, daily and session budgets, cost recorded even for failed turns, owner-only promotion management, per-session preferences, store promotion weeks, language switch)
- the daily spend ledger, including its in-memory fallback when the database is down
- graceful degradation when the promotions database is unreachable (e.g. a paused Supabase project)

Live runs: planning four dinners for two around 50 imported AH promotions took 19 tool steps and
cost **$0.43**, landing at €24.72 against a €35 budget (screenshot above); a four-item,
ten-store comparison cost about $0.19.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/pip install -r requirements-api.txt
.venv/bin/uvicorn api.main:app --port 8000   # web app: http://localhost:8000 (owner page: /admin.html)
.venv/bin/streamlit run app.py               # or the Streamlit UI: http://localhost:8501
.venv/bin/python -m grocery_agent            # or chat in the terminal
.venv/bin/pytest -q
```

## Deploy

**Web app (recommended):** one small always-on server with Docker, the FastAPI app and Caddy for
HTTPS. Step by step: [`deploy/DEPLOY.md`](deploy/DEPLOY.md).

**Streamlit Community Cloud** (free, but the app sleeps when idle and wakes slowly):

1. Push this repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create an app from the repo with `app.py`.
3. Under *Advanced settings → Secrets*, paste the settings from
   [`.streamlit/secrets.example.toml`](.streamlit/secrets.example.toml). At minimum set
   `SHARED_DEPLOYMENT = "true"`. Add `PROMOS_DATABASE_URL` (for example a free Supabase Postgres)
   so imported promotions and the daily spend survive restarts.
4. Also set a monthly spend limit in the Anthropic Console as a last line of defence.

| Setting | Purpose |
|---|---|
| `SHARED_DEPLOYMENT` | Per-visitor preferences; prices refresh automatically |
| `ANTHROPIC_API_KEY` | Your key; without `ACCESS_CODE` the app is open to everyone on it |
| `DAILY_BUDGET_EUR` | Free budget per day for all visitors together (resets at midnight, Amsterdam) |
| `SESSION_BUDGET_EUR` | Spend cap per browser session, so one visitor can't use up the day (default €1) |
| `USD_TO_EUR` | Exchange rate for the budgets, since Anthropic bills in USD (default 0.92) |
| `ADMIN_CODE` | Unlocks importing and deleting promotions for the owner |
| `ACCESS_CODE` | Optional: require a code before anyone can use the shared key |
| `PROMOS_DATABASE_URL` | SQLAlchemy / Postgres URL for promotions and the daily spend (default: local SQLite) |

## Limitations and next steps

- Promotions are imported by hand (screenshot or paste). Fetching them automatically would
  mean unofficial supermarket APIs, which their terms of service likely forbid.
- The Lidl feed often lacks pack sizes (about 75%); those items are estimated as one pack.
- The daily budget is checked before each request, so simultaneous requests can overshoot it by a few tens of cents. The per-session cap can be reset by reloading the page; the daily budget cannot.
- Next: an evaluation set for cross-store product matching (precision and recall), and a
  comparison of estimated against actual receipt totals.

## Credits

Price data: [supermarkt/checkjebon](https://github.com/supermarkt/checkjebon) (MIT).
Prices are indicative; the shelf price in store applies. Not affiliated with any supermarket.

MIT licensed.
