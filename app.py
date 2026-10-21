"""Streamlit UI.  Run:  .venv/bin/streamlit run app.py

Configuration (env vars locally, Streamlit secrets when deployed) is described in
grocery_agent/settings.py.
"""
from __future__ import annotations

import hmac
from datetime import timedelta

import anthropic
import streamlit as st

from grocery_agent import db, prefs, promos, settings
from grocery_agent.agent import PRICE, Agent
from grocery_agent.i18n import EXAMPLES, LANGUAGES, translator

st.set_page_config(page_title="Boodschappen Agent", page_icon="🛒", layout="wide")

SHARED = settings.flag("SHARED_DEPLOYMENT")
SERVER_KEY = settings.get("ANTHROPIC_API_KEY")
ACCESS_CODE = settings.get("ACCESS_CODE")
BUDGET = settings.number("SESSION_BUDGET_USD", 1.00)

def describe_tool(t, name: str, args: dict) -> str:
    if name == "search_products":
        stores = ", ".join(args.get("stores") or []) or t("tool_all_stores")
        return t("tool_search", query=args.get("query", ""), stores=stores)
    if name == "price_shopping_list":
        items = args.get("items") or []
        return t("tool_basket", items=", ".join(i.get("ingredient", "?") for i in items))
    if name == "list_promotions":
        return t("tool_promos") + (f" ({args['store']})" if args.get("store") else "")
    if name == "list_stores":
        return t("tool_stores")
    if name == "remember_preference":
        return t("tool_remember", note=args.get("note", ""))
    if name == "forget_preference":
        return t("tool_forget", note=args.get("note", ""))
    return f"⚙️ {name}"


# ---------------------------------------------------------------- shared resources

@st.cache_resource(ttl=timedelta(hours=6), show_spinner=False)
def prices_ready() -> str:
    """Refresh prices at most once per day; shared by all sessions on this server."""
    db.ensure_fresh(max_age=timedelta(hours=24))
    return db.data_date(db.connect())


# ---------------------------------------------------------------- session state

ss = st.session_state
if "history" not in ss:
    ss.agent = None
    ss.history = []            # [{"role", "content", "steps"}]
    ss.import_result = None
    ss.import_cost = 0.0       # $ spent on promotion imports this session
    ss.access_ok = False
    ss.prefs_store = prefs.MemoryStore() if SHARED else prefs.FileStore()
prefs.use_store(ss.prefs_store)  # each visitor gets their own preferences on a shared deployment


def session_cost() -> float:
    return (ss.agent.cost if ss.agent else 0.0) + ss.import_cost


def current_client() -> tuple[anthropic.Anthropic | None, str | None]:
    """The API client for this session and whose key it uses ('own' or 'server')."""
    own = (ss.get("user_key") or "").strip()
    if own:
        key, mode = own, "own"
    elif SERVER_KEY and (not ACCESS_CODE or ss.access_ok):
        key, mode = SERVER_KEY, "server"
    else:
        return None, None
    if ss.get("_client_key") != key:  # build once per key, keep it in this session only
        ss._client = anthropic.Anthropic(api_key=key)
        ss._client_key = key
    return ss._client, mode


# ---------------------------------------------------------------- language (first: everything below is translated)

with st.sidebar:
    # The option labels are what goes in the URL (?lang=中文), so use them as the values.
    label = st.segmented_control("Language / 语言", list(LANGUAGES.values()), default="English",
                                 required=True, key="lang", bind="query-params",
                                 label_visibility="collapsed")
lang = {v: k for k, v in LANGUAGES.items()}.get(label, "en")
t = translator(lang)

try:
    stale = db.age()
    if stale is None or stale > timedelta(hours=24):
        with st.spinner(t("prices_loading")):
            prices_ready()
    else:
        prices_ready()
    con = db.connect()
except Exception as e:  # download failed and no local copy
    st.error(t("prices_failed", error=e))
    st.stop()


# ---------------------------------------------------------------- sidebar: API key

with st.sidebar:
    st.header("🛒 Boodschappen Agent")

    st.subheader(t("api_key_header"))
    local_key_only = SERVER_KEY and not ACCESS_CODE and not SHARED
    with st.expander(t("own_key_expander"), expanded=not local_key_only):
        st.text_input(t("api_key_label"), type="password", key="user_key",
                      placeholder="sk-ant-...",
                      help=t("key_help"))
        if SERVER_KEY and ACCESS_CODE and not ss.access_ok:
            code = st.text_input(t("access_code"), type="password", key="access_code_input")
            if code:
                if hmac.compare_digest(code.encode(), ACCESS_CODE.encode()):
                    ss.access_ok = True
                    st.rerun()
                st.error(t("access_wrong"))

    client, key_mode = current_client()
    over_budget = key_mode == "server" and SHARED and session_cost() >= BUDGET
    if key_mode == "own":
        st.success(t("using_own_key"), icon=":material/key:")
    elif key_mode == "server" and SHARED:
        st.info(t("using_shared_key", spent=session_cost(), budget=BUDGET), icon=":material/group:")
    elif key_mode == "server":
        st.caption(t("using_local_key"))
    else:
        st.warning(t("need_key_or_code") if SERVER_KEY and ACCESS_CODE else t("need_key"))
    can_call = client is not None and not over_budget

    # ------------------------------------------------------------ price data
    st.subheader(t("prices"))
    stores = db.stores(con)
    st.caption(t("prices_caption", date=db.data_date(con), stores=len(stores),
                 products=sum(n for *_, n in stores)))
    if not SHARED and st.button(t("prices_update"), width="stretch"):
        with st.spinner(t("prices_updating")):
            db.download()
            db.build()
        prices_ready.clear()
        st.rerun()

    # ------------------------------------------------------------ promotions
    st.subheader(t("promos"))
    engine = promos.connect()
    active = promos.list_active(engine)
    if active:
        for pr in active:
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.markdown(f"**{pr['store'].upper()}** · {pr['title']}  \n"
                        f":small[{t('promo_line', label=pr['label'], n=pr['n_products'], until=pr['valid_to'] or '?')}]")
            if c2.button("✕", key=f"del-promo-{pr['id']}", help=t("promo_delete")):
                promos.delete(engine, pr["id"])
                st.rerun()
    else:
        st.caption(t("promos_none"))

    with st.expander(t("promos_import"), expanded=not active):
        store_options = {code: name for code, name, _ in stores}
        store = st.selectbox(t("store"), list(store_options), format_func=lambda c: store_options[c])
        # Keyed per store, so switching store resets the dates to that store's promotion week.
        valid = st.date_input(t("valid"), promos.promo_week(store), format="DD-MM-YYYY",
                              key=f"valid-{store}")
        files = st.file_uploader(t("files"), type=["png", "jpg", "jpeg", "webp", "pdf"],
                                 accept_multiple_files=True)
        text = st.text_area(t("paste"), height=100, placeholder=t("paste_placeholder"))
        if SHARED:
            st.caption(t("promos_shared_note"))
        if st.button(t("import_button"), type="primary", disabled=not can_call or not (files or text.strip()),
                     width="stretch"):
            vf, vt = (valid[0].isoformat(), valid[1].isoformat()) if len(valid) == 2 else (None, None)
            from grocery_agent.importer import import_promotions
            with st.spinner(t("importing")):
                try:
                    res = import_promotions(store, files=[(f.name, f.getvalue()) for f in files or []],
                                            text=text, valid_from=vf, valid_to=vt, client=client)
                    ss.import_result = res
                    ss.import_cost += (res.input_tokens * PRICE["input"]
                                       + res.output_tokens * PRICE["output"]) / 1_000_000
                except anthropic.AuthenticationError:
                    st.error(t("import_bad_key"))
                except Exception as e:  # show any failure in the sidebar, keep the app alive
                    st.error(t("import_failed", error=e))
            if ss.import_result:
                st.rerun()

        res = ss.import_result
        if res:
            st.success(t("imported", n=len(res.saved)))
            for r in res.saved:
                more = " …" if len(r["products"]) > 4 else ""
                st.markdown(f"- **{r['title']}** · {r['promotion']}  \n"
                            f"  :small[{', '.join(r['products'][:4])}{more}]")
            if res.unmatched:
                st.warning(t("unmatched", items="; ".join(f"{r['title']} ({r['promotion']})"
                                                          for r in res.unmatched)))

    if st.button(t("promos_cleanup"), width="stretch"):
        n = promos.delete_expired(engine)
        st.toast(t("promos_removed", n=n))
        st.rerun()

    # ------------------------------------------------------------ preferences
    st.subheader(t("prefs"))
    if SHARED:
        st.caption(t("prefs_session_note"))
    notes = prefs.load()
    for i, note in enumerate(notes):
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        c1.write(note)
        if c2.button("✕", key=f"del-pref-{i}"):
            prefs.save([n for n in notes if n != note])
            st.rerun()
    with st.form("add-pref", clear_on_submit=True, border=False):
        new = st.text_input(t("prefs_add"), placeholder=t("prefs_placeholder"),
                            label_visibility="collapsed")
        if st.form_submit_button(t("prefs_add_button")) and new.strip():
            prefs.save([*notes, new.strip()])
            st.rerun()

    # ------------------------------------------------------------ usage
    st.subheader(t("usage"))
    if ss.agent or ss.import_cost:
        u = ss.agent.usage if ss.agent else {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        st.caption(t("usage_caption", cost=session_cost(),
                     inp=u["input"] + u["cache_read"] + u["cache_write"], out=u["output"]))
    if st.button(t("new_chat"), width="stretch"):
        ss.agent = None
        ss.history = []
        st.rerun()


# ---------------------------------------------------------------- chat

st.title(t("title"))

if client is None:
    how_key = t("intro_with_code") if SERVER_KEY and ACCESS_CODE else t("intro_own_key")
    st.info(t("intro_body", how_key=how_key), title=t("intro_title"), icon=":material/waving_hand:")

if over_budget:
    st.warning(t("over_budget", budget=BUDGET))

for msg in ss.history:
    with st.chat_message(msg["role"]):
        if msg.get("steps"):
            with st.expander(t("steps", n=len(msg["steps"]))):
                st.markdown("\n\n".join(msg["steps"]))
        st.markdown(msg["content"])

prompt = st.chat_input(t("chat_placeholder"), disabled=not can_call)
if not ss.history and can_call:
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLES[lang]):
        if cols[i % 2].button(ex, key=f"ex-{i}", width="stretch"):
            prompt = ex

if prompt and can_call:
    ss.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    if ss.agent is None:
        ss.agent = Agent(client=client, on_tool=None)
    agent = ss.agent
    agent.client = client  # the user may have switched keys mid-conversation
    steps: list[str] = []
    with st.chat_message("assistant"):
        status = st.status(t("thinking"), expanded=True)

        def on_tool(name: str, args: dict) -> None:
            steps.append(describe_tool(t, name, args))
            status.markdown(steps[-1])
            status.update(label=t("working", n=len(steps)))

        agent.on_tool = on_tool
        checkpoint = len(agent.messages)
        try:
            answer = agent.ask(prompt)
            status.update(label=t("done", n=len(steps)), state="complete", expanded=False)
        except anthropic.APIError as e:
            del agent.messages[checkpoint:]  # drop the half-finished turn
            status.update(label=t("error"), state="error")
            if isinstance(e, anthropic.AuthenticationError):
                answer = t("err_auth")
            elif isinstance(e, anthropic.RateLimitError):
                answer = t("err_rate")
            elif isinstance(e, anthropic.APIStatusError):
                answer = t("err_status", code=e.status_code, message=e.message)
            else:
                answer = t("err_conn")
        st.markdown(answer)
    ss.history.append({"role": "assistant", "content": answer, "steps": steps})
    st.rerun()
