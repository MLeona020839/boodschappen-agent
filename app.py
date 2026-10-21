"""Streamlit UI.  Run:  .venv/bin/streamlit run app.py"""
from __future__ import annotations

import os
from datetime import date, timedelta

import anthropic
import streamlit as st

from grocery_agent import db, prefs, promos
from grocery_agent.agent import Agent

st.set_page_config(page_title="Boodschappen Agent", page_icon="🛒", layout="wide")

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


# ---------------------------------------------------------------- state

if "agent" not in st.session_state:
    st.session_state.agent = None
    st.session_state.history = []  # [{"role", "content", "steps"}]
    st.session_state.import_result = None


def get_agent() -> Agent:
    if st.session_state.agent is None:
        st.session_state.agent = Agent(on_tool=None)
    return st.session_state.agent


has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
try:
    con = db.connect()
except FileNotFoundError:
    con = None


# ---------------------------------------------------------------- sidebar

def this_week() -> tuple[date, date]:
    monday = date.today() - timedelta(days=date.today().weekday())
    return monday, monday + timedelta(days=6)


with st.sidebar:
    st.header("🛒 Boodschappen Agent")

    # --- price data
    st.subheader("价格数据")
    if con:
        total = sum(n for *_, n in db.stores(con))
        st.caption(f"数据日期 {db.data_date(con)} · {len(db.stores(con))} 家超市 · {total:,} 个商品")
    else:
        st.warning("还没有价格数据，请点下面的按钮下载。")
    if st.button("⬇️ 更新价格", use_container_width=True):
        with st.spinner("正在下载并建库（约 20 秒）…"):
            db.download()
            info = db.build()
        st.success(f"已更新：{sum(info['counts'].values()):,} 个商品")
        st.rerun()

    # --- promotions
    st.subheader("本周折扣")
    pcon = promos.connect()
    active = promos.list_active(pcon)
    if active:
        for pr in active:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{pr['store'].upper()}** · {pr['title']}  \n"
                        f"<small>{pr['label']} · {pr['n_products']} 个商品 · 至 {pr['valid_to'] or '?'}</small>",
                        unsafe_allow_html=True)
            if c2.button("✕", key=f"del-promo-{pr['id']}", help="删除这条折扣"):
                promos.delete(pcon, pr["id"])
                st.rerun()
    else:
        st.caption("还没有导入折扣。现在按常规价格比价。")

    with st.expander("➕ 导入折扣", expanded=not active):
        if not con:
            st.info("先下载价格数据。")
        else:
            store_options = {code: name for code, name, _ in db.stores(con)}
            store = st.selectbox("超市", list(store_options), format_func=lambda c: store_options[c])
            monday, sunday = this_week()
            valid = st.date_input("有效期", (monday, sunday), format="DD-MM-YYYY")
            files = st.file_uploader("传单截图 / 照片 / PDF", type=["png", "jpg", "jpeg", "webp", "pdf"],
                                     accept_multiple_files=True)
            text = st.text_area("或粘贴折扣页面的文字", height=100,
                                placeholder="例如从 ah.nl/bonus 复制的内容")
            if st.button("识别并导入", type="primary", disabled=not has_key or not (files or text.strip()),
                         use_container_width=True):
                vf, vt = (valid[0].isoformat(), valid[1].isoformat()) if len(valid) == 2 else (None, None)
                from grocery_agent.importer import import_promotions
                with st.spinner("Claude 正在识别折扣并匹配商品…"):
                    try:
                        st.session_state.import_result = import_promotions(
                            store, files=[(f.name, f.getvalue()) for f in files or []], text=text,
                            valid_from=vf, valid_to=vt)
                    except Exception as e:  # show any failure in the sidebar, keep the app alive
                        st.error(f"导入失败：{e}")
                st.rerun()

        res = st.session_state.import_result
        if res:
            st.success(f"导入 {len(res.saved)} 条折扣")
            for r in res.saved:
                st.markdown(f"- **{r['title']}** · {r['promotion']}  \n  <small>{', '.join(r['products'][:4])}"
                            f"{' …' if len(r['products']) > 4 else ''}</small>", unsafe_allow_html=True)
            if res.unmatched:
                st.warning("没找到对应商品：" + "；".join(f"{r['title']}（{r['promotion']}）" for r in res.unmatched))

    if st.button("清理过期折扣", use_container_width=True):
        n = promos.delete_expired(pcon)
        st.toast(f"删除了 {n} 条过期折扣")
        st.rerun()

    # --- preferences
    st.subheader("我的偏好")
    notes = prefs.load()
    for i, note in enumerate(notes):
        c1, c2 = st.columns([5, 1])
        c1.write(note)
        if c2.button("✕", key=f"del-pref-{i}"):
            prefs.save([n for n in notes if n != note])
            st.rerun()
    with st.form("add-pref", clear_on_submit=True, border=False):
        new = st.text_input("添加偏好", placeholder="例如：不吃猪肉", label_visibility="collapsed")
        if st.form_submit_button("添加") and new.strip():
            prefs.save([*notes, new.strip()])
            st.rerun()

    # --- usage
    st.subheader("本次对话")
    agent = st.session_state.agent
    if agent:
        u = agent.usage
        st.caption(f"约 ${agent.cost:.3f} · 输入 {u['input'] + u['cache_read'] + u['cache_write']:,} "
                   f"/ 输出 {u['output']:,} tokens")
    if st.button("🆕 新对话", use_container_width=True):
        st.session_state.agent = None
        st.session_state.history = []
        st.rerun()


# ---------------------------------------------------------------- chat

st.title("这周吃什么，去哪买最便宜？")

if not has_key:
    st.error("没有找到 ANTHROPIC_API_KEY。请在终端里执行 `source ~/.zshrc` 后再启动，"
             "或者用 `ANTHROPIC_API_KEY=... streamlit run app.py`。")
if not con:
    st.stop()

for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        if msg.get("steps"):
            with st.expander(f"查询过程（{len(msg['steps'])} 步）"):
                st.markdown("\n\n".join(msg["steps"]))
        st.markdown(msg["content"])

prompt = st.chat_input("用中文、英文或荷兰语提问…", disabled=not has_key)
if not st.session_state.history and has_key:
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 2].button(ex, key=f"ex-{i}", use_container_width=True):
            prompt = ex

if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    agent = get_agent()
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
        except anthropic.AuthenticationError:
            del agent.messages[checkpoint:]
            status.update(label="出错了", state="error")
            answer = "API key 无效。请检查 ANTHROPIC_API_KEY。"
        except anthropic.RateLimitError:
            del agent.messages[checkpoint:]
            status.update(label="出错了", state="error")
            answer = "请求太频繁，请稍等一会儿再试。"
        except anthropic.APIStatusError as e:
            del agent.messages[checkpoint:]
            status.update(label="出错了", state="error")
            answer = f"API 出错（{e.status_code}）：{e.message}"
        except anthropic.APIConnectionError:
            del agent.messages[checkpoint:]
            status.update(label="出错了", state="error")
            answer = "连不上 API，请检查网络。"
        st.markdown(answer)
    st.session_state.history.append({"role": "assistant", "content": answer, "steps": steps})
    st.rerun()
