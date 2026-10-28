// Boodschappen Agent web client: plain JS, no build step.
"use strict";

// Brand colours as text badges (not logos): recognisable, without implying affiliation.
const STORES = {
  ah:        { label: "AH",        bg: "#00A0E2", fg: "#ffffff" },
  jumbo:     { label: "Jumbo",     bg: "#FFD200", fg: "#1a1a1a" },
  lidl:      { label: "Lidl",      bg: "#0050AA", fg: "#FFF000" },
  plus:      { label: "PLUS",      bg: "#00843D", fg: "#ffffff" },
  dirk:      { label: "Dirk",      bg: "#E30613", fg: "#ffffff" },
  hoogvliet: { label: "Hoogvliet", bg: "#003D7C", fg: "#ffffff" },
  dekamarkt: { label: "DekaMarkt", bg: "#F18700", fg: "#ffffff" },
  spar:      { label: "SPAR",      bg: "#00843D", fg: "#ffffff", border: "#E30613" },
  poiesz:    { label: "Poiesz",    bg: "#8A1538", fg: "#ffffff" },
  vomar:     { label: "Vomar",     bg: "#D6001C", fg: "#FFE600" },
};

const $ = (sel) => document.querySelector(sel);
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

let T = null;          // { languages, text, examples }
let lang = "en";
let state = null;      // /api/state
let busy = false;
let sessionId = store.get("ba.session", null);
if (!sessionId) { sessionId = newSessionId(); store.set("ba.session", sessionId); }
let chatLog = store.get("ba.history", []);   // [{role, content, steps}]; not window.history
let prefs = store.get("ba.prefs", []);

function newSessionId() {
  return (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/[^A-Za-z0-9]/g, "");
}

// ---------------------------------------------------------------- i18n

// Python-style placeholders: {name}, {name:.2f}, {name:,}
function fmt(template, vars = {}) {
  return template.replace(/\{(\w+)(?::([^}]*))?\}/g, (m, key, spec) => {
    const v = vars[key];
    if (v === undefined || v === null) return m;
    if (spec === ",") return Number(v).toLocaleString("en-US");
    const fixed = /^\.(\d)f$/.exec(spec || "");
    if (fixed) return Number(v).toFixed(Number(fixed[1]));
    return String(v);
  });
}
function t(key, vars) {
  const table = (T && (T.text[lang] || T.text.en)) || {};
  const text = table[key] ?? (T && T.text.en[key]) ?? key;
  return fmt(text, vars);
}

function pickLanguage() {
  const param = new URLSearchParams(location.search).get("lang");
  const byLabel = { English: "en", Nederlands: "nl", "中文": "zh" };
  const fromUrl = byLabel[param] || param;
  if (["en", "nl", "zh"].includes(fromUrl)) return fromUrl;
  const saved = store.get("ba.lang", null);
  if (saved) return saved;
  const nav = (navigator.language || "en").toLowerCase();
  return nav.startsWith("nl") ? "nl" : nav.startsWith("zh") ? "zh" : "en";
}

function setLanguage(code) {
  lang = code;
  store.set("ba.lang", code);
  const url = new URL(location.href);
  if (code === "en") url.searchParams.delete("lang"); else url.searchParams.set("lang", code);
  window.history.replaceState(null, "", url);
  document.documentElement.lang = code === "zh" ? "zh-CN" : code;
  applyText();
}

function applyText() {
  document.querySelectorAll("[data-t]").forEach((el) => { el.textContent = t(el.dataset.t); });
  document.querySelectorAll("[data-t-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.tPlaceholder); });
  document.querySelectorAll("[data-t-title]").forEach((el) => { el.title = t(el.dataset.tTitle).replace(/^\S+\s/, ""); });
  document.querySelectorAll("[data-t-aria]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.tAria)); });
  document.querySelectorAll(".langs button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  renderExamples();
  renderState();
  renderPrefs();
  renderHistory();
}

// ---------------------------------------------------------------- rendering helpers

function badge(code, small = false) {
  const s = STORES[code] || { label: code.toUpperCase(), bg: "#5f6f65", fg: "#fff" };
  const el = document.createElement("span");
  el.className = "badge" + (small ? " sm" : "");
  el.textContent = s.label;
  el.style.background = s.bg;
  el.style.color = s.fg;
  if (s.border) el.style.boxShadow = `inset 0 -3px 0 ${s.border}`;
  return el;
}

function markdown(text) {
  if (!window.marked || !window.DOMPurify) {
    const pre = document.createElement("div");
    pre.style.whiteSpace = "pre-wrap";
    pre.textContent = text;
    return pre.outerHTML;
  }
  return DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: false }));
}

function describeStep(step) {
  const a = step.args || {};
  const frag = document.createDocumentFragment();
  const add = (s) => frag.append(document.createTextNode(s));
  switch (step.name) {
    case "search_products": {
      add(`🔍 ${a.query || ""} `);
      const codes = a.stores && a.stores.length ? a.stores : null;
      if (codes) codes.forEach((c) => { frag.append(badge(c, true)); add(" "); });
      else add(`(${t("tool_all_stores")})`);
      break;
    }
    case "price_shopping_list":
      add(t("tool_basket", { items: (a.items || []).map((i) => i.ingredient).join(", ") }).replace(/\*\*/g, ""));
      break;
    case "list_promotions":
      add(t("tool_promos") + " ");
      if (a.store) frag.append(badge(a.store, true));
      break;
    case "list_stores": add(t("tool_stores")); break;
    case "remember_preference": add(t("tool_remember", { note: a.note || "" })); break;
    case "forget_preference": add(t("tool_forget", { note: a.note || "" })); break;
    default: add(`⚙️ ${step.name}`);
  }
  return frag;
}

// ---------------------------------------------------------------- chat history

function stepsBlock(steps, running) {
  const det = document.createElement("details");
  det.className = "steps";
  if (running) det.open = true;
  const sum = document.createElement("summary");
  if (running) { const sp = document.createElement("span"); sp.className = "spinner"; sum.append(sp); }
  const label = document.createElement("span");
  label.textContent = running ? (steps.length ? t("working", { n: steps.length }) : t("thinking")) : t("steps", { n: steps.length });
  sum.append(label);
  det.append(sum);
  const ol = document.createElement("ol");
  steps.forEach((s) => { const li = document.createElement("li"); li.append(describeStep(s)); ol.append(li); });
  det.append(ol);
  return det;
}

function messageEl(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role === "user" ? "user" : "bot"}${m.error ? " error" : ""}`;
  if (m.role === "user") { el.textContent = m.content; return el; }
  if (m.steps && m.steps.length) el.append(stepsBlock(m.steps, false));
  const body = document.createElement("div");
  body.className = "body";
  body.innerHTML = markdown(m.content || "");
  el.append(body);
  return el;
}

function renderHistory() {
  const box = $("#messages");
  box.querySelectorAll(".msg").forEach((n) => n.remove());
  chatLog.forEach((m) => box.append(messageEl(m)));
  $("#examples").hidden = chatLog.length > 0;
  $("#new-chat").hidden = chatLog.length === 0;
  box.scrollTop = box.scrollHeight;
}

function renderExamples() {
  const box = $("#examples");
  box.replaceChildren();
  ((T && (T.examples[lang] || T.examples.en)) || []).forEach((ex) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = ex;
    b.addEventListener("click", () => send(ex));
    box.append(b);
  });
}

function showNotice(text) {
  const n = $("#notice");
  n.textContent = text || "";
  n.hidden = !text;
}

function setBusy(on) {
  busy = on;
  $("#send").disabled = on || isBlocked();
  $("#input").disabled = isBlocked();
}

// ---------------------------------------------------------------- state panel

function isBlocked() {
  const b = state && state.budget;
  if (!b) return false;
  return (b.day_left !== null && b.day_left <= 0) || b.session_spent >= b.session_total;
}

function renderState() {
  if (!T) return;
  const badges = $("#store-badges");
  badges.replaceChildren();
  const codes = state ? state.stores.map((s) => s.code) : Object.keys(STORES);
  codes.forEach((c) => badges.append(badge(c)));
  if (state) {
    const products = state.stores.reduce((n, s) => n + s.products, 0);
    $("#data-line").textContent = t("web_stores_compared", { stores: state.stores.length, products, date: state.data_date });
    renderBudget(state.budget);
    renderPromos(state.promotions, state.promotions_available);
  }
}

function renderBudget(b) {
  const el = $("#budget");
  if (!b || b.day_total === null || b.day_total === undefined) { el.hidden = true; }
  else {
    el.hidden = false;
    el.textContent = t("web_free_today", { left: b.day_left, total: b.day_total });
    el.className = "budget" + (b.day_left <= 0 ? " empty" : b.day_left < b.day_total * 0.2 ? " low" : "");
  }
  if (b && b.day_left !== null && b.day_left <= 0) showNotice(t("day_exhausted", { total: b.day_total }));
  else if (b && b.session_spent >= b.session_total) showNotice(t("session_exhausted", { session: b.session_total }));
  setBusy(busy);
}

function renderPromos(list, available) {
  const box = $("#promos");
  box.replaceChildren();
  if (!available) { const p = document.createElement("p"); p.className = "muted small"; p.textContent = t("promos_unavailable"); box.append(p); return; }
  if (!list.length) { const p = document.createElement("p"); p.className = "muted small"; p.textContent = t("promos_none"); box.append(p); return; }
  const det = document.createElement("details");
  det.className = "promos";
  const sum = document.createElement("summary");
  sum.textContent = t("active_promos", { n: list.length });
  det.append(sum);
  const ul = document.createElement("ul");
  ul.className = "promo-list";
  list.forEach((p) => {
    const li = document.createElement("li");
    li.append(badge(p.store, true));
    const text = document.createElement("span");
    const deal = document.createElement("span");
    deal.className = "deal";
    deal.textContent = p.label;
    text.append(document.createTextNode(p.title + " · "), deal);
    li.append(text);
    ul.append(li);
  });
  det.append(ul);
  box.append(det);
}

function renderPrefs() {
  const ul = $("#prefs");
  ul.replaceChildren();
  prefs.forEach((note) => {
    const li = document.createElement("li");
    li.append(document.createTextNode(note));
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.setAttribute("aria-label", "Remove");
    x.addEventListener("click", () => { prefs = prefs.filter((p) => p !== note); store.set("ba.prefs", prefs); renderPrefs(); });
    li.append(x);
    ul.append(li);
  });
}

async function loadState() {
  try {
    const r = await fetch(`api/state?session_id=${encodeURIComponent(sessionId)}`);
    if (!r.ok) throw new Error(r.status);
    state = await r.json();
    renderState();
  } catch {
    $("#data-line").textContent = t("web_loading");
    setTimeout(loadState, 4000);  // prices may still be downloading after a restart
  }
}

// ---------------------------------------------------------------- sending

async function send(text) {
  text = (text || "").trim();
  if (!text || busy || isBlocked()) return;
  showNotice("");
  $("#input").value = "";
  autosize();
  chatLog.push({ role: "user", content: text });
  renderHistory();

  const box = $("#messages");
  const live = document.createElement("div");
  live.className = "msg bot";
  const steps = [];
  let stepsEl = stepsBlock(steps, true);
  const body = document.createElement("div");
  body.className = "body";
  live.append(stepsEl, body);
  box.append(live);
  box.scrollTop = box.scrollHeight;

  let streamed = "";
  let frame = 0;
  const paint = () => {
    frame = 0;
    body.innerHTML = markdown(streamed);
    box.scrollTop = box.scrollHeight;
  };
  const refreshSteps = () => { const fresh = stepsBlock(steps, true); live.replaceChild(fresh, stepsEl); stepsEl = fresh; };

  setBusy(true);
  let answer = null, error = null;
  try {
    const r = await fetch("api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text, prefs }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      if (data.budget && state) { state.budget = data.budget; renderBudget(data.budget); }
      error = { 429: data.error === "rate_limited" ? t("web_rate_limited") : null, 409: t("web_busy"), 503: t("web_unavailable") }[r.status]
        || (data.error === "day_exhausted" ? t("day_exhausted", { total: data.budget?.day_total })
          : data.error === "session_exhausted" ? t("session_exhausted", { session: data.budget?.session_total })
          : t("err_server"));
    } else {
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          if (!chunk.startsWith("data: ")) continue;
          const ev = JSON.parse(chunk.slice(6));
          if (ev.type === "step") { steps.push(ev); streamed = ""; refreshSteps(); paint(); }
          else if (ev.type === "text") { streamed += ev.delta; if (!frame) frame = requestAnimationFrame(paint); }
          else if (ev.type === "prefs") { prefs = ev.notes; store.set("ba.prefs", prefs); renderPrefs(); }
          else if (ev.type === "done") { answer = ev.answer; }
          else if (ev.type === "error") { error = ev.code === "rate" ? t("err_rate") : ev.code === "conn" ? t("err_conn") : ev.code === "auth" ? t("web_unavailable") : t("err_server"); }
          else if (ev.type === "budget" && state) { state.budget = ev.budget; renderBudget(ev.budget); }
        }
      }
    }
  } catch {
    error = t("err_conn");
  }
  if (frame) cancelAnimationFrame(frame);
  live.remove();
  if (answer !== null && !error) {
    chatLog.push({ role: "assistant", content: answer, steps: steps.map(({ name, args }) => ({ name, args })) });
  } else {
    chatLog.push({ role: "assistant", content: error || t("err_server"), error: true });
  }
  // Failed turns are not saved: they are not part of the agent's conversation either.
  store.set("ba.history", chatLog.filter((m, i, all) => !m.error && !(all[i + 1] && all[i + 1].error)).slice(-40));
  renderHistory();
  setBusy(false);
}

async function newChat() {
  chatLog = [];
  store.set("ba.history", chatLog);
  renderHistory();
  showNotice("");
  fetch("api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId }) }).catch(() => {});
  if (state) renderBudget(state.budget);
}

function autosize() {
  const ta = $("#input");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

// ---------------------------------------------------------------- start

document.addEventListener("DOMContentLoaded", async () => {
  lang = pickLanguage();
  document.querySelectorAll(".langs button").forEach((b) => b.addEventListener("click", () => setLanguage(b.dataset.lang)));
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#input").value); }
  });
  $("#input").addEventListener("input", autosize);
  $("#new-chat").addEventListener("click", newChat);
  $("#pref-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#pref-input").value.trim();
    if (v && !prefs.includes(v)) { prefs.push(v); store.set("ba.prefs", prefs); renderPrefs(); }
    $("#pref-input").value = "";
  });
  renderHistory();
  try { T = await (await fetch("api/i18n")).json(); } catch { T = null; }
  setLanguage(lang);
  loadState();
});
