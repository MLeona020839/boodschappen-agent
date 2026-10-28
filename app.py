"""Streamlit UI.  Run:  .venv/bin/streamlit run app.py

Layout: chat on the left, a tools panel on the right (promotion import, preferences, spend), so
screenshots dragged from the bottom-right corner of the screen land right next to the upload box.
With a server key and no access code the app is open to everyone, within a shared daily budget
(DAILY_BUDGET_EUR) and a per-session budget (SESSION_BUDGET_EUR). Only the owner (ADMIN_CODE) can
import or delete promotions on a shared deployment.

Configuration (env vars locally, Streamlit secrets when deployed) is described in
grocery_agent/settings.py.
"""
from __future__ import annotations

import hmac
import importlib
from datetime import timedelta

import anthropic
import sqlalchemy as sa
import streamlit as st

from grocery_agent import agent as agent_mod
from grocery_agent import db, i18n, prefs, promos, quota, settings
from grocery_agent.agent import PRICE, Agent

# After a git push, Streamlit Cloud reruns app.py but can keep older copies of imported modules.
# The UI strings change most often and hold no state, so always load the current ones.
i18n = importlib.reload(i18n)
EXAMPLES, LANGUAGES, translator = i18n.EXAMPLES, i18n.LANGUAGES, i18n.translator

st.set_page_config(page_title="Boodschappen Agent", page_icon="🛒", layout="wide")

SHARED = settings.flag("SHARED_DEPLOYMENT")
SERVER_KEY = settings.get("ANTHROPIC_API_KEY")
ACCESS_CODE = settings.get("ACCESS_CODE")
ADMIN_CODE = settings.get("ADMIN_CODE")
SESSION_BUDGET_EUR = settings.number("SESSION_BUDGET_EUR", 1.00)
OPEN_SERVER_KEY = bool(SERVER_KEY) and not ACCESS_CODE  # open to everyone on the server's key


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
    ss.access_ok = False       # entered the right access code
    ss.api_key = ""            # the visitor's own key; kept in this session only
    ss.admin_ok = False        # entered the owner's admin code
    ss.prefs_store = prefs.MemoryStore() if SHARED else prefs.FileStore()
prefs.use_store(ss.prefs_store)  # each visitor gets their own preferences on a shared deployment


def session_cost() -> float:
    """US dollars spent in this browser session."""
    return (ss.agent.cost if ss.agent else 0.0) + ss.import_cost


def current_client() -> tuple[anthropic.Anthropic | None, str | None]:
    """The API client for this session and whose key it uses ('own' or 'server')."""
    if ss.api_key:
        key, mode = ss.api_key, "own"
    elif SERVER_KEY and (OPEN_SERVER_KEY or ss.access_ok):
        key, mode = SERVER_KEY, "server"
    else:
        return None, None
    if ss.get("_client_key") != key:  # build once per key, keep it in this session only
        ss._client = anthropic.Anthropic(api_key=key)
        ss._client_key = key
    return ss._client, mode


# ---------------------------------------------------------------- header + language

head_left, head_right = st.columns([4, 1], vertical_alignment="bottom")
with head_right:
    # The option labels are what goes in the URL (?lang=中文), so use them as the values.
    label = st.segmented_control("Language / 语言", list(LANGUAGES.values()), default="English",
                                 required=True, key="lang", bind="query-params",
                                 label_visibility="collapsed")
lang = {v: k for k, v in LANGUAGES.items()}.get(label, "en")
t = translator(lang)
head_left.title(t("title"))

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

client, key_mode = current_client()


@st.cache_data(ttl=timedelta(hours=1), show_spinner=False)
def shared_key_works(key: str) -> bool | None:
    """Check the server's key once an hour, so a wrong secret shows up before anyone asks."""
    return agent_mod.check_key(key)


# ---------------------------------------------------------------- gate: access code or own key

if client is None:
    _, middle, _ = st.columns([1, 2, 1])
    with middle, st.container(border=True):
        st.subheader(t("gate_title"))
        st.write(t("gate_body"))
        with st.form("gate", border=False):
            code = ""
            if SERVER_KEY and ACCESS_CODE:
                code = st.text_input(t("gate_code"), type="password", key="gate_code",
                                     help=t("gate_code_help"))
                st.caption(t("gate_or"))
            key = st.text_input(t("gate_key"), type="password", key="gate_key",
                                placeholder="sk-ant-...", help=t("key_help"))
            submitted = st.form_submit_button(t("gate_start"), type="primary", key="gate_submit",
                                              width="stretch")
        if submitted:
            code, key = code.strip(), key.strip()
            if code:
                if hmac.compare_digest(code.encode(), ACCESS_CODE.encode()):
                    ss.access_ok = True
                    st.rerun()
                st.error(t("access_wrong"))
            elif key:
                ok = agent_mod.check_key(key)
                if ok is False:
                    st.error(t("gate_bad_key"))
                else:
                    if ok is None:
                        st.toast(t("gate_unverified"))
                    ss.api_key = key
                    st.rerun()
            else:
                st.warning(t("gate_empty") if SERVER_KEY and ACCESS_CODE else t("gate_empty_key"))
        st.caption(t("gate_get_key"))
    st.stop()

# Free use on the server's key is limited per day (all visitors) and per session.
on_server_key = key_mode == "server" and SHARED
day_left = quota.remaining_eur() if on_server_key else None
session_spent_eur = session_cost() * quota.usd_to_eur()
day_exhausted = day_left is not None and day_left <= 0
session_exhausted = on_server_key and session_spent_eur >= SESSION_BUDGET_EUR
can_call = not (day_exhausted or session_exhausted)
can_manage = not SHARED or ss.admin_ok  # import / delete promotions

chat_col, panel = st.columns([2, 1], gap="large")


# ---------------------------------------------------------------- right panel

with panel:
    # --- whose key
    status_box = st.container(horizontal=True, vertical_alignment="center")
    if key_mode == "own":
        status_box.success(t("using_own_key"), icon=":material/key:")
    elif shared_key_works(SERVER_KEY) is False:
        status_box.error(t("shared_key_rejected"), icon=":material/key_off:")
    elif on_server_key and day_left is not None:
        status_box.info(t("free_left", left=day_left, total=quota.daily_budget_eur(),
                          spent=session_spent_eur, session=SESSION_BUDGET_EUR), icon=":material/redeem:")
    elif on_server_key:
        status_box.info(t("session_left", spent=session_spent_eur, session=SESSION_BUDGET_EUR),
                        icon=":material/group:")
    if not OPEN_SERVER_KEY and status_box.button(t("change_key"), type="tertiary", key="change_key"):
        ss.api_key, ss.access_ok = "", False
        st.rerun()

    # --- promotions
    st.subheader(t("promos"))
    try:
        engine = promos.connect()
        active = promos.list_active(engine)
    except (promos.Unavailable, sa.exc.SQLAlchemyError) as e:
        if not isinstance(e, promos.Unavailable):
            promos.mark_down(e)
        engine, active = None, []
        st.warning(t("promos_unavailable"), icon=":material/cloud_off:")

    if active:
        with st.expander(t("active_promos", n=len(active)), expanded=False):
            for pr in active:
                c1, c2 = st.columns([5, 1], vertical_alignment="center")
                line = t("promo_line", label=pr["label"], n=pr["n_products"], until=pr["valid_to"] or "?")
                c1.markdown(f"**{pr['store'].upper()}** · {pr['title']}  \n:small[{line}]")
                if can_manage and c2.button("✕", key=f"del-promo-{pr['id']}", help=t("promo_delete")):
                    promos.delete(engine, pr["id"])
                    st.rerun()
            if can_manage and st.button(t("promos_cleanup"), width="stretch"):
                n = promos.delete_expired(engine)
                st.toast(t("promos_removed", n=n))
                st.rerun()
    elif engine is not None:
        st.caption(t("promos_none"))

    stores = db.stores(con)
    if engine is not None and SHARED and not ss.admin_ok:
        # Visitors can use the promotions; only the owner can add or remove them.
        with st.expander(t("admin_title"), expanded=False):
            if not ADMIN_CODE:
                st.caption(t("admin_not_set"))
            else:
                with st.form("admin", border=False):
                    code = st.text_input(t("admin_code"), type="password", key="admin_code_input")
                    if st.form_submit_button(t("admin_unlock"), key="admin_submit"):
                        if hmac.compare_digest(code.strip().encode(), ADMIN_CODE.encode()):
                            ss.admin_ok = True
                            st.rerun()
                        st.error(t("access_wrong"))
    if engine is not None and can_manage:
        with st.expander(t("promos_import"), expanded=not active):
            store_options = {c: n for c, n, _ in stores}
            # No default store: importing a Dirk folder as "AH" was too easy.
            store = st.selectbox(t("store"), list(store_options), index=None,
                                 format_func=lambda c: store_options[c], placeholder=t("store_choose"))
            # Keyed per store, so switching store resets the dates to that store's promotion week.
            valid = st.date_input(t("valid"), promos.promo_week(store), format="DD-MM-YYYY",
                                  key=f"valid-{store}")
            # A new key after each successful import gives an empty upload box and text area.
            upload_round = ss.get("upload_round", 0)
            files = st.file_uploader(t("files"), type=["png", "jpg", "jpeg", "webp", "pdf"],
                                     key=f"files-{upload_round}",
                                     accept_multiple_files=True)
            text = st.text_area(t("paste"), height=100, placeholder=t("paste_placeholder"),
                                key=f"paste-{upload_round}")
            if SHARED:
                st.caption(t("promos_shared_note"))
            has_input = bool(files or text.strip())
            if store is None:
                st.caption(t("import_needs_store"))
            elif not has_input:
                st.caption(t("import_needs_input"))
            if st.button(t("import_button"), type="primary", disabled=not (can_call and has_input and store),
                         width="stretch"):
                vf, vt = (valid[0].isoformat(), valid[1].isoformat()) if len(valid) == 2 else (None, None)
                from grocery_agent.importer import import_promotions
                with st.spinner(t("importing")):
                    try:
                        res = import_promotions(store, files=[(f.name, f.getvalue()) for f in files or []],
                                                text=text, valid_from=vf, valid_to=vt, client=client)
                        ss.import_result = res
                        cost = (res.input_tokens * PRICE["input"] + res.output_tokens * PRICE["output"]) / 1_000_000
                        ss.import_cost += cost
                        if on_server_key:
                            quota.record(cost)
                        ss.upload_round = upload_round + 1  # clear the inputs; on failure they stay for a retry
                    except anthropic.AuthenticationError:
                        st.error(t("import_bad_key") if key_mode == "own" else t("shared_key_rejected"))
                    except Exception as e:  # show any failure in the panel, keep the app alive
                        st.error(t("import_failed", error=e))
                if ss.import_result:
                    st.rerun()

            res = ss.import_result
            if res:
                if res.switched_from:
                    names = {c: n for c, n, _ in stores}
                    st.info(t("store_switched", chosen=names.get(res.switched_from, res.switched_from),
                              seen=names.get(res.store, res.store)), icon=":material/swap_horiz:")
                st.success(t("imported", n=len(res.saved)))
                for r in res.saved:
                    more = " …" if len(r["products"]) > 4 else ""
                    st.markdown(f"- **{r['title']}** · {r['promotion']}  \n"
                                f"  :small[{', '.join(r['products'][:4])}{more}]")
                if res.unmatched:
                    st.warning(t("unmatched", items="; ".join(f"{r['title']} ({r['promotion']})"
                                                              for r in res.unmatched)))

    # --- preferences
    notes = prefs.load()
    with st.expander(t("prefs_count", n=len(notes)), expanded=False):
        if SHARED:
            st.caption(t("prefs_session_note"))
        for i, note in enumerate(notes):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.write(note)
            if c2.button("✕", key=f"del-pref-{i}"):
                prefs.save([n for n in notes if n != note])
                st.rerun()
        with st.form("add-pref", clear_on_submit=True, border=False):
            new = st.text_input(t("prefs_add"), placeholder=t("prefs_placeholder"),
                                label_visibility="collapsed")
            if st.form_submit_button(t("prefs_add_button"), key="add_pref") and new.strip():
                prefs.save([*notes, new.strip()])
                st.rerun()

    # --- prices and spend
    with st.expander(t("panel_more"), expanded=False):
        st.caption(t("prices_caption", date=db.data_date(con), stores=len(stores),
                     products=sum(n for *_, n in stores)))
        if not SHARED and st.button(t("prices_update"), width="stretch"):
            with st.spinner(t("prices_updating")):
                db.download()
                db.build()
            prices_ready.clear()
            st.rerun()
        u = ss.agent.usage if ss.agent else {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        st.caption(t("usage_caption", cost=session_cost(),
                     inp=u["input"] + u["cache_read"] + u["cache_write"], out=u["output"]))


# ---------------------------------------------------------------- chat (left)

with chat_col:
    if day_exhausted:
        st.warning(t("day_exhausted", total=quota.daily_budget_eur()), icon=":material/bedtime:")
    elif session_exhausted:
        st.warning(t("session_exhausted", session=SESSION_BUDGET_EUR), icon=":material/hourglass_bottom:")

    messages = st.container(height=560, border=False, autoscroll=True)
    with messages:
        for msg in ss.history:
            with st.chat_message(msg["role"]):
                if msg.get("steps"):
                    with st.expander(t("steps", n=len(msg["steps"]))):
                        st.markdown("\n\n".join(msg["steps"]))
                st.markdown(msg["content"])

    prompt = None
    if not ss.history and can_call:
        with messages:
            cols = st.columns(2)
            for i, ex in enumerate(EXAMPLES[lang]):
                if cols[i % 2].button(ex, key=f"ex-{i}", width="stretch"):
                    prompt = ex

    bottom = st.container(horizontal=True, vertical_alignment="center")
    typed = bottom.chat_input(t("chat_placeholder"), disabled=not can_call, max_chars=1000)
    if ss.history and bottom.button(":material/add_comment:", help=t("new_chat"), key="new_chat"):
        ss.agent = None
        ss.history = []
        st.rerun()
    prompt = typed or prompt

    if prompt and can_call:
        # The prompt goes into the history together with its answer (below), so a turn that
        # is interrupted by a rerun leaves no orphaned question behind.
        with messages:
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
                cost_before = agent.cost
                try:
                    answer = agent.ask(prompt)  # rolls itself back if interrupted
                    status.update(label=t("done", n=len(steps)), state="complete", expanded=False)
                except anthropic.APIError as e:
                    status.update(label=t("error"), state="error")
                    if isinstance(e, anthropic.AuthenticationError):
                        answer = t("err_auth") if key_mode == "own" else t("shared_key_rejected")
                    elif isinstance(e, anthropic.RateLimitError):
                        answer = t("err_rate")
                    elif isinstance(e, anthropic.APIStatusError):
                        answer = t("err_status", code=e.status_code, message=e.message)
                    else:
                        answer = t("err_conn")
                finally:
                    # Tokens already used count against today's budget even if the turn failed.
                    if on_server_key:
                        quota.record(agent.cost - cost_before)
                st.markdown(answer)
        ss.history.append({"role": "user", "content": prompt})
        ss.history.append({"role": "assistant", "content": answer, "steps": steps})
        st.rerun()
