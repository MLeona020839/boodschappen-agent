# Security review: OWASP Top 10 for LLM Applications

Reviewed on 2026-11-11 against the [OWASP Top 10 for LLM Applications (2025)](https://genai.owasp.org/llm-top-10/),
covering the web app (FastAPI on Cloud Run + static page on GitHub Pages), the agent and its tools,
the promotion importer, and the MCP server. The review read the code, checked the live service
where that was safe and free (rate limits, via the price search), and fixed what it found; every fix
has a test.

## Findings and fixes

| # | Risk | Finding | Status |
|---|---|---|---|
| 1 | LLM10 Unbounded consumption | Rate limits keyed on the **first** `X-Forwarded-For` entry, which the client controls. Confirmed on the live service: a forged header gave a fresh rate-limit bucket per request. Combined with new browser sessions, one person could skip the per-IP and per-visitor caps and use up the whole day's shared budget, and also bypass the admin login throttle. | Fixed: the visitor IP is the entry the proxy appends (the last one). `/api/health?forwarding=1` shows a caller their own header, to confirm this on a deployment. |
| 2 | LLM10 Unbounded consumption | The budget is checked when a question starts and charged when it ends, so many parallel questions could overshoot the daily cap. | Fixed: at most `MAX_CONCURRENT_CHATS` (default 3) agent runs per instance; with at most 3 instances the overshoot is bounded to a few euros. |
| 3 | LLM10 / brute force | The admin login throttle was per IP only (and per-IP could be forged, see 1). | Fixed: also 30 attempts per hour for all addresses together. |
| 4 | LLM01 Prompt injection | Visitor-written preferences were placed in the system prompt with the operator's instructions, giving them the same authority. | Fixed: wrapped in `<user_preferences>` and marked as the user's data that cannot change the instructions. |
| 5 | LLM01 / LLM04 Indirect injection, data poisoning | Promotion titles from third-party folders reach the chat agent through `list_promotions`, with no length bound. | Fixed: stored titles and labels are capped (160 / 60 characters). Extraction is schema-constrained and matching only accepts product ids from the candidates, so folder text can't add tools or products. |
| 6 | LLM05 Improper output handling | Product links in the price comparison came straight from third-party price data. | Fixed: only `https:` links are rendered. Agent answers were already sanitized (DOMPurify) and the rest of the UI uses `textContent`. |
| 7 | LLM03 Supply chain | The Docker image installed the latest version of every dependency at each deploy. It had silently moved to anthropic 2.x, a major version the tests had not run against (it changes how the tool loop talks to the API). | Fixed: `requirements-api.lock` pins the exact versions the tests pass with; the image installs from it. |

## Checked, no change needed

- **LLM02 Sensitive information disclosure**: the API key, admin code and database URL live only in
  server environment variables (the deploy file is git- and gcloud-ignored); no secrets in the git
  history; error responses carry no internals; the agent's tools can't read configuration.
- **LLM06 Excessive agency**: the agent's tools are read-only (search, price, list) apart from saving
  the visitor's own preferences in their browser. Importing and deleting promotions needs an
  HMAC-signed admin token (12 h). The MCP server exposes only read-only tools.
- **Server-side requests** (web-address import): https only, supermarket and allefolders domains only,
  redirects must stay on the same site, size-capped downloads and decompression.
- **LLM07 System prompt leakage**: the system prompt holds no secrets.
- **LLM08 Vector and embedding weaknesses**: not applicable (no embeddings or vector store).
- **LLM09 Misinformation**: totals come from the pricing tool, not the model; the page states that
  shelf prices in store apply.
- **SQL**: all queries are parameterized; full-text search terms are quoted.

## Accepted risks

- The server is stateless: the browser sends the visible chat back, so a visitor can edit their own
  history. This only affects their own conversation, within their own budget.
- Imported promotions are only as good as the folders; the owner sees every import's result and can
  delete promotions on the admin page.
- Spend caps are enforced by this app. Keep a monthly spend limit in the Anthropic Console as well.
