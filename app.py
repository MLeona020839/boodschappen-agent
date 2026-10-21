"""Streamlit UI.  Run:  .venv/bin/streamlit run app.py

Configuration (env vars locally, Streamlit secrets when deployed) is described in
grocery_agent/settings.py.
"""
from __future__ import annotations

import hmac
from datetime import date, timedelta

import anthropic
import streamlit as st

from grocery_agent import db, prefs, promos, settings
from grocery_agent.agent import PRICE, Agent

st.set_page_config(page_title="Boodschappen Agent", page_icon="🛒", layout="wide")

SHARED = settings.flag("SHARED_DEPLOYMENT")
SERVER_KEY = settings.get("ANTHROPIC_API_KEY")
ACCESS_CODE = settings.get("ACCESS_CODE")
BUDGET = settings.number("SESSION_BUDGET_USD", 1.00)

EXAMPLES = [
    "我们家两个人，平时去 AH 和 Lidl，不吃香菜",
    "这周帮我规划 4 顿晚饭，预算 €35，想吃亚洲菜",
    "鸡胸肉、鸡蛋、牛奶、香蕉，哪家最便宜？",
    "这周 AH 有什么折扣适合做晚饭？",
]


def describe_tool(name: str, args: dict) -> str:
    if name == "search_products":
        stores = ", ".join(args.get("stores") or []) or "所有超市"
        return f"🔍 搜索 **{args.get('query', '')}**（{stores}）"
    if name == "price_shopping_list":
        items = args.get("items") or []
        return f"🧮 计算购物篮：{', '.join(i.get('ingredient', '?') for i in items)}"
    if name == "list_promotions":
        return "🏷️ 查看本周折扣" + (f"（{args['store']}）" if args.get("store") else "")
    if name == "list_stores":
        return "🏪 查看超市列表"
    if name == "remember_preference":
        return f"💾 记住：{args.get('note', '')}"
    if name == "forget_preference":
        return f"🗑️ 忘记：{args.get('note', '')}"
    return f"⚙️ {name}"


def this_week() -> tuple[date, date]:
    monday = date.today() - timedelta(days=date.today().weekday())
    return monday, monday + timedelta(days=6)


# ---------------------------------------------------------------- shared resources

@st.cache_resource(ttl=timedelta(hours=6), show_spinner="正在下载最新价格（第一次约 20 秒）…")
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


try:
    prices_ready()
    con = db.connect()
except Exception as e:  # download failed and no local copy
    st.error(f"价格数据加载失败：{e}")
    st.stop()


# ---------------------------------------------------------------- sidebar: API key

with st.sidebar:
    st.header("🛒 Boodschappen Agent")

    st.subheader("API key")
    local_key_only = SERVER_KEY and not ACCESS_CODE and not SHARED
    with st.expander("使用自己的 API key", expanded=not local_key_only):
        st.text_input("Anthropic API key", type="password", key="user_key",
                      placeholder="sk-ant-...",
                      help="只保存在你这次打开的页面里，不会写入文件，也不会被其他人看到。"
                           "在 console.anthropic.com 的 API Keys 页面创建。")
        if SERVER_KEY and ACCESS_CODE and not ss.access_ok:
            code = st.text_input("或者输入邀请码，使用共享 key", type="password")
            if code:
                if hmac.compare_digest(code.encode(), ACCESS_CODE.encode()):
                    ss.access_ok = True
                    st.rerun()
                st.error("邀请码不对")

    client, key_mode = current_client()
    over_budget = key_mode == "server" and SHARED and session_cost() >= BUDGET
    if key_mode == "own":
        st.success("正在使用你自己的 key", icon=":material/key:")
    elif key_mode == "server" and SHARED:
        st.info(f"正在使用共享 key · 本次额度 ${session_cost():.2f} / ${BUDGET:.2f}",
                icon=":material/group:")
    elif key_mode == "server":
        st.caption("正在使用本机的 ANTHROPIC_API_KEY")
    else:
        st.warning("请填写你的 API key" + ("或邀请码" if SERVER_KEY and ACCESS_CODE else "") + " 才能开始。")
    can_call = client is not None and not over_budget

    # ------------------------------------------------------------ price data
    st.subheader("价格数据")
    stores = db.stores(con)
    st.caption(f"数据日期 {db.data_date(con)} · {len(stores)} 家超市 · "
               f"{sum(n for *_, n in stores):,} 个商品 · 每天自动更新")
    if not SHARED and st.button("⬇️ 立即更新价格", width="stretch"):
        with st.spinner("正在下载并建库（约 20 秒）…"):
            db.download()
            db.build()
        prices_ready.clear()
        st.rerun()

    # ------------------------------------------------------------ promotions
    st.subheader("本周折扣")
    engine = promos.connect()
    active = promos.list_active(engine)
    if active:
        for pr in active:
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.markdown(f"**{pr['store'].upper()}** · {pr['title']}  \n"
                        f":small[{pr['label']} · {pr['n_products']} 个商品 · 至 {pr['valid_to'] or '?'}]")
            if c2.button("✕", key=f"del-promo-{pr['id']}", help="删除这条折扣"):
                promos.delete(engine, pr["id"])
                st.rerun()
    else:
        st.caption("还没有导入折扣。现在按常规价格比价。")

    with st.expander("➕ 导入折扣", expanded=not active):
        store_options = {code: name for code, name, _ in stores}
        store = st.selectbox("超市", list(store_options), format_func=lambda c: store_options[c])
        monday, sunday = this_week()
        valid = st.date_input("有效期", (monday, sunday), format="DD-MM-YYYY")
        files = st.file_uploader("传单截图 / 照片 / PDF", type=["png", "jpg", "jpeg", "webp", "pdf"],
                                 accept_multiple_files=True)
        text = st.text_area("或粘贴折扣页面的文字", height=100,
                            placeholder="例如从 ah.nl/bonus 复制的内容")
        if SHARED:
            st.caption("导入的折扣所有人都能看到和使用。")
        if st.button("识别并导入", type="primary", disabled=not can_call or not (files or text.strip()),
                     width="stretch"):
            vf, vt = (valid[0].isoformat(), valid[1].isoformat()) if len(valid) == 2 else (None, None)
            from grocery_agent.importer import import_promotions
            with st.spinner("Claude 正在识别折扣并匹配商品…"):
                try:
                    res = import_promotions(store, files=[(f.name, f.getvalue()) for f in files or []],
                                            text=text, valid_from=vf, valid_to=vt, client=client)
                    ss.import_result = res
                    ss.import_cost += (res.input_tokens * PRICE["input"]
                                       + res.output_tokens * PRICE["output"]) / 1_000_000
                except anthropic.AuthenticationError:
                    st.error("API key 无效，请检查。")
                except Exception as e:  # show any failure in the sidebar, keep the app alive
                    st.error(f"导入失败：{e}")
            if ss.import_result:
                st.rerun()

        res = ss.import_result
        if res:
            st.success(f"导入 {len(res.saved)} 条折扣")
            for r in res.saved:
                more = " …" if len(r["products"]) > 4 else ""
                st.markdown(f"- **{r['title']}** · {r['promotion']}  \n"
                            f"  :small[{', '.join(r['products'][:4])}{more}]")
            if res.unmatched:
                st.warning("没找到对应商品：" + "；".join(f"{r['title']}（{r['promotion']}）"
                                                     for r in res.unmatched))

    if st.button("清理过期折扣", width="stretch"):
        n = promos.delete_expired(engine)
        st.toast(f"删除了 {n} 条过期折扣")
        st.rerun()

    # ------------------------------------------------------------ preferences
    st.subheader("我的偏好")
    if SHARED:
        st.caption("只在这次打开的页面里有效，刷新后会清空。")
    notes = prefs.load()
    for i, note in enumerate(notes):
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        c1.write(note)
        if c2.button("✕", key=f"del-pref-{i}"):
            prefs.save([n for n in notes if n != note])
            st.rerun()
    with st.form("add-pref", clear_on_submit=True, border=False):
        new = st.text_input("添加偏好", placeholder="例如：不吃猪肉", label_visibility="collapsed")
        if st.form_submit_button("添加") and new.strip():
            prefs.save([*notes, new.strip()])
            st.rerun()

    # ------------------------------------------------------------ usage
    st.subheader("本次花费")
    if ss.agent or ss.import_cost:
        u = ss.agent.usage if ss.agent else {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        st.caption(f"约 ${session_cost():.3f} · 对话输入 {u['input'] + u['cache_read'] + u['cache_write']:,} "
                   f"/ 输出 {u['output']:,} tokens")
    if st.button("🆕 新对话", width="stretch"):
        ss.agent = None
        ss.history = []
        st.rerun()


# ---------------------------------------------------------------- chat

st.title("这周吃什么，去哪买最便宜？")

if client is None:
    st.info(
        "告诉它你想吃什么、预算多少，它会查 10 家荷兰超市的价格，给出菜单、购物清单，"
        "以及去哪家（或哪两家）买最便宜。\n\n"
        "**开始前**：在左侧填写你的 Anthropic API key。没有的话，可以在 "
        "[console.anthropic.com](https://console.anthropic.com) 注册、充值后，在 API Keys 页面创建。"
        "费用按用量算在你自己的账户上，规划一周菜单大约 $0.1–0.5。",
        title="怎么用", icon=":material/waving_hand:",
    )

if over_budget:
    st.warning(f"共享 key 在这次会话里的额度（${BUDGET:.2f}）已经用完。"
               "可以在左侧填写你自己的 API key 继续使用。")

for msg in ss.history:
    with st.chat_message(msg["role"]):
        if msg.get("steps"):
            with st.expander(f"查询过程（{len(msg['steps'])} 步）"):
                st.markdown("\n\n".join(msg["steps"]))
        st.markdown(msg["content"])

prompt = st.chat_input("用中文、英文或荷兰语提问…", disabled=not can_call)
if not ss.history and can_call:
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLES):
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
        status = st.status("正在思考…", expanded=True)

        def on_tool(name: str, args: dict) -> None:
            steps.append(describe_tool(name, args))
            status.markdown(steps[-1])
            status.update(label=f"正在查价…（{len(steps)} 步）")

        agent.on_tool = on_tool
        checkpoint = len(agent.messages)
        try:
            answer = agent.ask(prompt)
            status.update(label=f"完成（{len(steps)} 步）", state="complete", expanded=False)
        except anthropic.APIError as e:
            del agent.messages[checkpoint:]  # drop the half-finished turn
            status.update(label="出错了", state="error")
            if isinstance(e, anthropic.AuthenticationError):
                answer = "API key 无效，请在左侧检查你填写的 key。"
            elif isinstance(e, anthropic.RateLimitError):
                answer = "请求太频繁，请稍等一会儿再试。"
            elif isinstance(e, anthropic.APIStatusError):
                answer = f"API 出错（{e.status_code}）：{e.message}"
            else:
                answer = "连不上 API，请检查网络。"
        st.markdown(answer)
    ss.history.append({"role": "assistant", "content": answer, "steps": steps})
    st.rerun()
