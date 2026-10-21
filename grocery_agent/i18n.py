"""UI strings in English and Chinese. The agent itself answers in whatever language the user writes."""
from __future__ import annotations

LANGUAGES = {"en": "English", "zh": "中文"}

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "title": "What's for dinner, and where is it cheapest?",
        "language": "Language",
        # API key
        "own_key_expander": "Use your own API key",
        "key_help": "Kept only in this browser tab: never written to disk or visible to anyone else. "
                    "Create one under API Keys at console.anthropic.com.",
        "access_code": "Or enter an access code to use the shared key",
        "access_wrong": "That access code is not right.",
        "using_own_key": "Using your own key",
        "using_shared_key": "Using the shared key · this session ${spent:.2f} / ${budget:.2f}",
        "using_local_key": "Using ANTHROPIC_API_KEY from this computer",
        "need_key": "Enter your API key to get started.",
        "need_key_or_code": "Enter your API key or an access code to get started.",
        # prices
        "prices": "Prices",
        "prices_caption": "Data from {date} · {stores} supermarkets · {products:,} products · refreshed daily",
        "prices_update": "⬇️ Update prices now",
        "prices_updating": "Downloading and indexing (about 20 seconds)…",
        "prices_loading": "Downloading the latest prices (about 20 seconds the first time)…",
        "prices_failed": "Could not load the price data: {error}",
        # promotions
        "promos": "This week's promotions",
        "promo_line": "{label} · {n} products · until {until}",
        "promo_delete": "Delete this promotion",
        "promos_none": "No promotions imported yet, so regular prices are used.",
        "promos_import": "➕ Import promotions",
        "store": "Supermarket",
        "valid": "Valid",
        "files": "Folder screenshots, photos or PDF",
        "paste": "Or paste the text of a promotions page",
        "paste_placeholder": "e.g. copied from ah.nl/bonus",
        "promos_shared_note": "Imported promotions are visible to and used by everyone.",
        "import_button": "Read and import",
        "importing": "Claude is reading the promotions and matching products…",
        "import_bad_key": "The API key is not valid. Please check it.",
        "import_failed": "Import failed: {error}",
        "imported": "Imported {n} promotions",
        "unmatched": "No matching products found for: {items}",
        "promos_cleanup": "Remove expired promotions",
        "promos_removed": "Removed {n} expired promotions",
        # preferences
        "prefs": "My preferences",
        "prefs_session_note": "Kept only in this browser tab; cleared when you reload.",
        "prefs_add": "Add preference",
        "prefs_placeholder": "e.g. no pork",
        "prefs_add_button": "Add",
        # usage
        "usage": "Spend this session",
        "usage_caption": "≈ ${cost:.3f} · chat input {inp:,} / output {out:,} tokens",
        "new_chat": "🆕 New conversation",
        # chat
        "intro_title": "How it works",
        "intro_body": "Tell it what you want to eat and your budget. It checks prices at 10 Dutch "
                      "supermarkets and gives you a meal plan, a shopping list, and which store "
                      "(or which two) is cheapest.\n\n"
                      "**Before you start**: open the sidebar (on a phone, tap **»** top left). {how_key} "
                      "You can create a key at [console.anthropic.com](https://console.anthropic.com) "
                      "after signing up and adding credit. Usage is billed to your own account; "
                      "planning a week of dinners costs about $0.10–0.50.",
        "intro_with_code": "If someone gave you an access code, just enter that. Otherwise enter your own Anthropic API key.",
        "intro_own_key": "Enter your Anthropic API key.",
        "over_budget": "The shared key's budget for this session (${budget:.2f}) is used up. "
                       "You can keep going by entering your own API key in the sidebar.",
        "steps": "How I got there ({n} steps)",
        "chat_placeholder": "Ask in English, Dutch, Chinese…",
        "thinking": "Thinking…",
        "working": "Checking prices… ({n} steps)",
        "done": "Done ({n} steps)",
        "error": "Something went wrong",
        "err_auth": "The API key is not valid. Please check it in the sidebar.",
        "err_rate": "Too many requests. Please wait a moment and try again.",
        "err_status": "API error ({code}): {message}",
        "err_conn": "Could not reach the API. Please check your connection.",
        # tool steps
        "tool_search": "🔍 Searching **{query}** ({stores})",
        "tool_all_stores": "all stores",
        "tool_basket": "🧮 Pricing the basket: {items}",
        "tool_promos": "🏷️ Checking this week's promotions",
        "tool_stores": "🏪 Listing supermarkets",
        "tool_remember": "💾 Remembering: {note}",
        "tool_forget": "🗑️ Forgetting: {note}",
    },
    "zh": {
        "title": "这周吃什么，去哪买最便宜？",
        "language": "语言",
        "own_key_expander": "使用自己的 API key",
        "key_help": "只保存在你这次打开的页面里，不会写入文件，也不会被其他人看到。"
                    "在 console.anthropic.com 的 API Keys 页面创建。",
        "access_code": "或者输入邀请码，使用共享 key",
        "access_wrong": "邀请码不对",
        "using_own_key": "正在使用你自己的 key",
        "using_shared_key": "正在使用共享 key · 本次额度 ${spent:.2f} / ${budget:.2f}",
        "using_local_key": "正在使用本机的 ANTHROPIC_API_KEY",
        "need_key": "请填写你的 API key，然后就可以开始了。",
        "need_key_or_code": "请填写你的 API key 或邀请码，然后就可以开始了。",
        "prices": "价格数据",
        "prices_caption": "数据日期 {date} · {stores} 家超市 · {products:,} 个商品 · 每天自动更新",
        "prices_update": "⬇️ 立即更新价格",
        "prices_updating": "正在下载并建库（约 20 秒）…",
        "prices_loading": "正在下载最新价格（第一次约 20 秒）…",
        "prices_failed": "价格数据加载失败：{error}",
        "promos": "本周折扣",
        "promo_line": "{label} · {n} 个商品 · 至 {until}",
        "promo_delete": "删除这条折扣",
        "promos_none": "还没有导入折扣。现在按常规价格比价。",
        "promos_import": "➕ 导入折扣",
        "store": "超市",
        "valid": "有效期",
        "files": "传单截图 / 照片 / PDF",
        "paste": "或粘贴折扣页面的文字",
        "paste_placeholder": "例如从 ah.nl/bonus 复制的内容",
        "promos_shared_note": "导入的折扣所有人都能看到和使用。",
        "import_button": "识别并导入",
        "importing": "Claude 正在识别折扣并匹配商品…",
        "import_bad_key": "API key 无效，请检查。",
        "import_failed": "导入失败：{error}",
        "imported": "导入 {n} 条折扣",
        "unmatched": "没找到对应商品：{items}",
        "promos_cleanup": "清理过期折扣",
        "promos_removed": "删除了 {n} 条过期折扣",
        "prefs": "我的偏好",
        "prefs_session_note": "只在这次打开的页面里有效，刷新后会清空。",
        "prefs_add": "添加偏好",
        "prefs_placeholder": "例如：不吃猪肉",
        "prefs_add_button": "添加",
        "usage": "本次花费",
        "usage_caption": "约 ${cost:.3f} · 对话输入 {inp:,} / 输出 {out:,} tokens",
        "new_chat": "🆕 新对话",
        "intro_title": "怎么用",
        "intro_body": "告诉它你想吃什么、预算多少，它会查 10 家荷兰超市的价格，给出菜单、购物清单，"
                      "以及去哪家（或哪两家）买最便宜。\n\n"
                      "**开始前**：打开左侧边栏（手机上点左上角的 **»**）。{how_key}"
                      "API key 可以在 [console.anthropic.com](https://console.anthropic.com) 注册、充值后，"
                      "在 API Keys 页面创建，费用按用量算在你自己的账户上，规划一周菜单大约 $0.1–0.5。",
        "intro_with_code": "如果有人给了你邀请码，直接填邀请码就行；否则请填写你自己的 Anthropic API key。",
        "intro_own_key": "请填写你的 Anthropic API key。",
        "over_budget": "共享 key 在这次会话里的额度（${budget:.2f}）已经用完。"
                       "可以在左侧填写你自己的 API key 继续使用。",
        "steps": "查询过程（{n} 步）",
        "chat_placeholder": "用中文、英文或荷兰语提问…",
        "thinking": "正在思考…",
        "working": "正在查价…（{n} 步）",
        "done": "完成（{n} 步）",
        "error": "出错了",
        "err_auth": "API key 无效，请在左侧检查你填写的 key。",
        "err_rate": "请求太频繁，请稍等一会儿再试。",
        "err_status": "API 出错（{code}）：{message}",
        "err_conn": "连不上 API，请检查网络。",
        "tool_search": "🔍 搜索 **{query}**（{stores}）",
        "tool_all_stores": "所有超市",
        "tool_basket": "🧮 计算购物篮：{items}",
        "tool_promos": "🏷️ 查看本周折扣",
        "tool_stores": "🏪 查看超市列表",
        "tool_remember": "💾 记住：{note}",
        "tool_forget": "🗑️ 忘记：{note}",
    },
}

EXAMPLES = {
    "en": [
        "Two of us, we usually shop at AH and Lidl, no coriander",
        "Plan 4 dinners this week, €35 budget, something Asian",
        "Chicken breast, eggs, milk, bananas: where is it cheapest?",
        "Which AH promotions this week are good for dinner?",
    ],
    "zh": [
        "我们家两个人，平时去 AH 和 Lidl，不吃香菜",
        "这周帮我规划 4 顿晚饭，预算 €35，想吃亚洲菜",
        "鸡胸肉、鸡蛋、牛奶、香蕉，哪家最便宜？",
        "这周 AH 有什么折扣适合做晚饭？",
    ],
}


def translator(lang: str):
    table = TEXT.get(lang, TEXT["en"])

    def t(key: str, **kwargs) -> str:
        text = table.get(key) or TEXT["en"][key]
        return text.format(**kwargs) if kwargs else text

    return t
