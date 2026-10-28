// Owner page: log in with ADMIN_CODE, import a week's promotions, delete or clean up.
"use strict";

const API = window.API_BASE ? window.API_BASE.replace(/\/+$/, "") + "/" : "";  // see config.js

const $ = (sel) => document.querySelector(sel);
let token = sessionStorage.getItem("ba.admin") || "";
let chosen = [];   // File objects

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: `Bearer ${token}` };
  const r = await fetch(path, { ...options, headers });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { logout(); throw new Error(data.detail || "Please log in again."); }
  if (!r.ok) throw new Error(data.detail || `Error ${r.status}`);
  return data;
}

function logout() {
  token = "";
  sessionStorage.removeItem("ba.admin");
  $("#panel").hidden = true;
  $("#login").hidden = false;
}

function showPanel() {
  $("#login").hidden = true;
  $("#panel").hidden = false;
  loadStores();
  loadRows();
}

async function loadStores() {
  const state = await (await fetch(`${API}api/state`)).json();
  const sel = $("#store");
  state.stores.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.code;
    o.textContent = s.name;
    sel.append(o);
  });
}

async function loadRows() {
  const { promotions } = await api(`${API}api/admin/promotions`);
  const body = $("#rows");
  body.replaceChildren();
  const today = new Date().toISOString().slice(0, 10);
  promotions.forEach((p) => {
    const tr = document.createElement("tr");
    const expired = p.valid_to && p.valid_to < today;
    [p.store.toUpperCase(), p.title, p.label, `${p.valid_from || "?"} – ${p.valid_to || "?"}${expired ? " (expired)" : ""}`, p.n_products]
      .forEach((v) => { const td = document.createElement("td"); td.textContent = v; tr.append(td); });
    const td = document.createElement("td");
    const del = document.createElement("button");
    del.className = "del";
    del.title = "Delete";
    del.textContent = "✕";
    del.addEventListener("click", async () => {
      await api(`${API}api/admin/promotions/${p.id}`, { method: "DELETE" });
      loadRows();
    });
    td.append(del);
    tr.append(td);
    if (expired) tr.style.opacity = ".5";
    body.append(tr);
  });
}

const MAX_FILES = 10;
const ACCEPTED = /^(image\/(png|jpeg|webp)|application\/pdf)$/;

// Adds to the list (dropping or picking again no longer replaces earlier files).
function addFiles(list) {
  const key = (f) => `${f.name}|${f.size}|${f.lastModified}`;
  const have = new Set(chosen.map(key));
  [...list].filter((f) => ACCEPTED.test(f.type) && !have.has(key(f))).forEach((f) => chosen.push(f));
  if (chosen.length > MAX_FILES) {
    chosen = chosen.slice(0, MAX_FILES);
    showMsg(`At most ${MAX_FILES} files per import.`, true);
  }
  renderFiles();
}

function clearFiles() {
  chosen = [];
  renderFiles();
}

function renderFiles() {
  const ul = $("#file-list");
  ul.replaceChildren();
  chosen.forEach((f, i) => {
    const li = document.createElement("li");
    li.append(document.createTextNode(`${f.name} (${Math.round(f.size / 1024)} KB) `));
    const x = document.createElement("button");
    x.type = "button";
    x.className = "del";
    x.title = "Remove";
    x.textContent = "✕";
    x.addEventListener("click", () => { chosen.splice(i, 1); renderFiles(); });
    li.append(x);
    ul.append(li);
  });
  $("#drop").textContent = chosen.length
    ? `${chosen.length} file${chosen.length > 1 ? "s" : ""} added. Drop, pick or paste (⌘V) more.`
    : "Drop folder screenshots, photos or a PDF here, click to choose, or paste a screenshot (⌘V)";
}

function showMsg(text, isError) {
  const msg = $("#import-msg");
  msg.textContent = text;
  msg.className = isError ? "small msg-err" : "small muted";
}

document.addEventListener("DOMContentLoaded", () => {
  if (token) showPanel();

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#login-msg").textContent = "";
    const r = await fetch(`${API}api/admin/login`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: $("#code").value }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { $("#login-msg").textContent = data.detail || "Login failed."; $("#login-msg").className = "small msg-err"; return; }
    token = data.token;
    sessionStorage.setItem("ba.admin", token);
    showPanel();
  });

  $("#store").addEventListener("change", async () => {
    const code = $("#store").value;
    if (!code) return;
    const w = await (await fetch(`${API}api/promo-week?store=${encodeURIComponent(code)}`)).json();
    $("#from").value = w.valid_from;
    $("#to").value = w.valid_to;
  });

  const drop = $("#drop");
  drop.addEventListener("click", () => $("#files").click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") $("#files").click(); });
  $("#files").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  // A screenshot taken to the clipboard (⌘⌃⇧4) can be pasted straight onto the page.
  document.addEventListener("paste", (e) => {
    if ($("#panel").hidden || e.target === $("#text")) return;
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    const stamp = new Date().toISOString().slice(11, 19).replace(/:/g, "");
    addFiles(files.map((f, i) => new File([f], `pasted-${stamp}-${i + 1}.png`, { type: f.type || "image/png" })));
  });

  $("#import-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#import-msg");
    if (!$("#store").value) { msg.textContent = "Choose the supermarket first."; msg.className = "small msg-err"; return; }
    if (!chosen.length && !$("#text").value.trim()) { msg.textContent = "Add screenshots or paste text."; msg.className = "small msg-err"; return; }
    const form = new FormData();
    form.append("store", $("#store").value);
    if ($("#from").value) form.append("valid_from", $("#from").value);
    if ($("#to").value) form.append("valid_to", $("#to").value);
    form.append("text", $("#text").value);
    chosen.forEach((f) => form.append("files", f, f.name));
    $("#import-btn").disabled = true;
    msg.textContent = "Claude is reading the promotions and matching products… (about a minute)";
    msg.className = "small muted";
    try {
      const res = await api(`${API}api/admin/import`, { method: "POST", body: form });
      const box = $("#result");
      box.replaceChildren();
      if (res.switched_from) {
        const p = document.createElement("p");
        p.textContent = `The folder is from ${res.store.toUpperCase()}, not ${res.switched_from.toUpperCase()}, so the promotions were imported for ${res.store.toUpperCase()}.`;
        box.append(p);
      }
      const h = document.createElement("p");
      h.className = "msg-ok";
      h.textContent = `Imported ${res.saved.length} promotions.`;
      box.append(h);
      const ul = document.createElement("ul");
      res.saved.forEach((r) => { const li = document.createElement("li"); li.textContent = `${r.title} · ${r.promotion} — ${r.products.slice(0, 4).join(", ")}${r.products.length > 4 ? " …" : ""}`; ul.append(li); });
      box.append(ul);
      if (res.unmatched.length) {
        const p = document.createElement("p");
        p.className = "msg-err";
        p.textContent = "No matching products found for: " + res.unmatched.map((r) => `${r.title} (${r.promotion})`).join("; ");
        box.append(p);
      }
      msg.textContent = "";
      clearFiles();
      $("#files").value = "";
      $("#text").value = "";
      loadRows();
    } catch (err) {
      msg.textContent = err.message;
      msg.className = "small msg-err";
    } finally {
      $("#import-btn").disabled = false;
    }
  });

  $("#cleanup").addEventListener("click", async () => {
    const { removed } = await api(`${API}api/admin/cleanup`, { method: "POST" });
    alert(`Removed ${removed} expired promotions.`);
    loadRows();
  });
});
