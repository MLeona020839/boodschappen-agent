"""FastAPI backend for the web frontend in web/.  Run:  .venv/bin/uvicorn api.main:app

Serves the static site and a small JSON API. Configuration comes from environment variables (see
grocery_agent/settings.py): ANTHROPIC_API_KEY, DAILY_BUDGET_EUR, SESSION_BUDGET_EUR, ADMIN_CODE,
PROMOS_DATABASE_URL, USD_TO_EUR, TRUST_PROXY=1 behind Caddy or Cloud Run, and CORS_ORIGINS when
the page is served from another origin (GitHub Pages).
"""
from __future__ import annotations

import collections
import hashlib
import hmac
import json
import logging
import queue
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import anthropic
import sqlalchemy as sa
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from grocery_agent import db, i18n, prefs, promos, quota, settings
from grocery_agent.agent import PRICE, Agent, history_from_text

log = logging.getLogger("api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_FILES = 10


# ---------------------------------------------------------------- shared state

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = settings.get("ANTHROPIC_API_KEY")
        if not key:
            raise HTTPException(503, "The server has no ANTHROPIC_API_KEY configured.")
        _client = anthropic.Anthropic(api_key=key)
    return _client


_prices_ready = threading.Event()

# The server keeps no conversations: each request carries the visible chat, so a restart or
# Cloud Run scaling to zero loses nothing. Only "one question at a time per visitor" is in memory.
_active: set[str] = set()
_active_lock = threading.Lock()


def _refresh_prices_forever(stop: threading.Event) -> None:
    """Keep the price database at most a day old, checking every hour."""
    while not stop.is_set():
        try:
            db.ensure_fresh(max_age=timedelta(hours=24))
            _prices_ready.set()
        except Exception:
            log.exception("price refresh failed")
            if db.DB_PATH.exists():
                _prices_ready.set()  # an older copy is better than nothing
        stop.wait(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    threading.Thread(target=_refresh_prices_forever, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Boodschappen Agent", lifespan=lifespan, docs_url=None, redoc_url=None)

# The web page may be served elsewhere (e.g. GitHub Pages): CORS_ORIGINS lists the origins allowed
# to call this API, e.g. "https://mleona020839.github.io". Same-origin use needs nothing.
_origins = [o.strip().rstrip("/") for o in (settings.get("CORS_ORIGINS") or "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Content-Type", "Authorization"], max_age=3600)


def need_prices() -> None:
    if not (_prices_ready.is_set() or db.DB_PATH.exists()):
        raise HTTPException(503, "Prices are still loading; try again in a few seconds.")


# ---------------------------------------------------------------- rate limiting

class RateLimiter:
    """Sliding-window limits per client IP; harder to get around than a per-session budget."""

    def __init__(self, limits: list[tuple[int, float]]):
        self.limits = limits  # (max requests, window seconds)
        self.hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        longest = max(w for _, w in self.limits)
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > longest:
                q.popleft()
            if any(sum(1 for t in q if now - t <= w) >= n for n, w in self.limits):
                return False
            q.append(now)
            return True


chat_limiter = RateLimiter([(5, 60), (30, 3600)])
admin_limiter = RateLimiter([(10, 600)])


def client_ip(request: Request) -> str:
    if settings.flag("TRUST_PROXY"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- budgets

def session_budget_eur() -> float:
    return settings.number("SESSION_BUDGET_EUR", 1.00)


def budget_state(session_id: str | None) -> dict:
    spent = (quota.session_spent_usd(session_id) if session_id else 0.0) * quota.usd_to_eur()
    return {
        "day_left": quota.remaining_eur(),
        "day_total": quota.daily_budget_eur(),
        "session_spent": round(spent, 4),
        "session_total": session_budget_eur(),
    }


def budget_block(state: dict) -> str | None:
    if state["day_left"] is not None and state["day_left"] <= 0:
        return "day_exhausted"
    if state["session_spent"] >= state["session_total"]:
        return "session_exhausted"
    return None


# ---------------------------------------------------------------- public endpoints

@app.get("/api/health")
def health():
    return {"ok": True, "prices_ready": _prices_ready.is_set(), "active": len(_active)}


@app.get("/api/i18n")
def strings():
    """UI strings, so the web page and the Streamlit app share one set of translations."""
    return JSONResponse({"languages": i18n.LANGUAGES, "text": i18n.TEXT, "examples": i18n.EXAMPLES},
                        headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/state")
def state(session_id: str | None = None):
    need_prices()
    con = db.connect()
    stores = [{"code": c, "name": n, "products": k} for c, n, k in db.stores(con)]
    active = promos.safely(promos.list_active, default=None)
    return {
        "data_date": db.data_date(con),
        "stores": stores,
        "budget": budget_state(session_id),
        "promotions_available": active is not None,
        "promotions": [_promo_json(p) for p in active or []],
        "admin_enabled": bool(settings.get("ADMIN_CODE")),
    }


def _promo_json(p: dict) -> dict:
    return {k: p[k] for k in ("id", "store", "title", "label", "kind", "valid_from", "valid_to", "n_products")}


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class ChatIn(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=1000)
    prefs: list[str] = Field(default_factory=list, max_length=30)
    history: list[Turn] = Field(default_factory=list, max_length=60)  # the visible chat so far


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    """Run one agent turn and stream it as server-sent events:
    step (tool call), text (generated text), prefs (updated preferences), done, error."""
    need_prices()
    if not SESSION_ID.match(body.session_id):
        raise HTTPException(400, "Invalid session id.")
    blocked = budget_block(budget_state(body.session_id))
    if blocked:
        return JSONResponse({"error": blocked, "budget": budget_state(body.session_id)}, status_code=429)
    if not chat_limiter.allow(client_ip(request)):
        return JSONResponse({"error": "rate_limited"}, status_code=429)

    client()  # fail now (503) if no API key, rather than inside the worker thread
    with _active_lock:
        if body.session_id in _active:
            return JSONResponse({"error": "busy"}, status_code=409)
        _active.add(body.session_id)
    visitor_prefs = prefs.MemoryStore([p[:200] for p in body.prefs])

    events: queue.Queue = queue.Queue()
    DONE = object()

    def emit(kind: str, **data) -> None:
        events.put({"type": kind, **data})

    def work() -> None:
        agent = Agent(client=client(),
                      on_tool=lambda name, args: emit("step", name=name, args=args),
                      on_text=lambda delta: emit("text", delta=delta))
        agent.messages = history_from_text([t.model_dump() for t in body.history])
        prefs.use_store(visitor_prefs)  # tools read this visitor's preferences (ContextVar, this thread)
        before_prefs = visitor_prefs.load()
        try:
            answer = agent.ask(body.message)
            if visitor_prefs.load() != before_prefs:
                emit("prefs", notes=visitor_prefs.load())
            emit("done", answer=answer)
        except anthropic.APIError as e:
            code = ("auth" if isinstance(e, anthropic.AuthenticationError) else
                    "rate" if isinstance(e, anthropic.RateLimitError) else
                    "status" if isinstance(e, anthropic.APIStatusError) else "conn")
            log.warning("chat failed: %s", e)
            emit("error", code=code)
        except Exception:
            log.exception("chat crashed")
            emit("error", code="server")
        finally:
            # tokens used count even if the turn failed
            quota.record(agent.cost, session_id=body.session_id)
            emit("budget", budget=budget_state(body.session_id))
            with _active_lock:
                _active.discard(body.session_id)
            events.put(DONE)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is DONE:
                return
            yield f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/promo-week")
def promo_week(store: str):
    start, end = promos.promo_week(store)
    return {"valid_from": start.isoformat(), "valid_to": end.isoformat()}


# ---------------------------------------------------------------- owner (admin) endpoints

TOKEN_HOURS = 12


def _sign(payload: str) -> str:
    code = settings.get("ADMIN_CODE") or ""
    return hmac.new(code.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(now: float | None = None) -> str:
    expires = int((now or time.time()) + TOKEN_HOURS * 3600)
    return f"{expires}.{_sign(f'admin:{expires}')}"


def require_admin(authorization: str = Header(default="")) -> None:
    if not settings.get("ADMIN_CODE"):
        raise HTTPException(403, "Admin is disabled: no ADMIN_CODE is configured.")
    token = authorization.removeprefix("Bearer ").strip()
    expires, _, signature = token.partition(".")
    if not (expires.isdigit() and int(expires) > time.time()
            and hmac.compare_digest(signature, _sign(f"admin:{expires}"))):
        raise HTTPException(401, "Please log in again.")


class LoginIn(BaseModel):
    code: str = Field(max_length=200)


@app.post("/api/admin/login")
def admin_login(body: LoginIn, request: Request):
    code = settings.get("ADMIN_CODE")
    if not code:
        raise HTTPException(403, "Admin is disabled: no ADMIN_CODE is configured.")
    if not admin_limiter.allow(client_ip(request)):
        raise HTTPException(429, "Too many attempts; wait a few minutes.")
    if not hmac.compare_digest(body.code.strip().encode(), code.encode()):
        raise HTTPException(401, "That code is not right.")
    return {"token": make_token()}


@app.get("/api/admin/promotions", dependencies=[Depends(require_admin)])
def admin_promotions():
    try:
        return {"promotions": [_promo_json(p) for p in promos.list_all(promos.connect())]}
    except (promos.Unavailable, sa.exc.SQLAlchemyError):
        raise HTTPException(503, "The promotions database is unavailable.")


@app.delete("/api/admin/promotions/{promo_id}", dependencies=[Depends(require_admin)])
def admin_delete(promo_id: int):
    promos.delete(promos.connect(), promo_id)
    return {"ok": True}


@app.post("/api/admin/cleanup", dependencies=[Depends(require_admin)])
def admin_cleanup():
    return {"removed": promos.delete_expired(promos.connect())}


@app.post("/api/admin/import", dependencies=[Depends(require_admin)])
def admin_import(store: str = Form(...), valid_from: str | None = Form(None), valid_to: str | None = Form(None),
                 text: str = Form(""), files: list[UploadFile] | None = File(None)):
    need_prices()
    if store not in {c for c, _, _ in db.stores(db.connect())}:
        raise HTTPException(400, "Unknown store.")
    for d in (valid_from, valid_to):
        if d:
            date.fromisoformat(d)  # raises on garbage
    files = files or []
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"At most {MAX_UPLOAD_FILES} files per import.")
    blobs = []
    for f in files:
        data = f.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"{f.filename} is larger than 8 MB.")
        blobs.append((f.filename or "upload", data))
    if not blobs and not text.strip():
        raise HTTPException(400, "Add screenshots or paste text.")

    from grocery_agent.importer import import_promotions
    try:
        res = import_promotions(store, files=blobs, text=text, valid_from=valid_from, valid_to=valid_to,
                                client=client())
    except anthropic.APIError as e:
        raise HTTPException(502, f"Claude request failed: {e.__class__.__name__}")
    quota.record((res.input_tokens * PRICE["input"] + res.output_tokens * PRICE["output"]) / 1_000_000)
    return {"store": res.store, "switched_from": res.switched_from, "saved": res.saved, "unmatched": res.unmatched}


# ---------------------------------------------------------------- static site (last, so /api wins)

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
