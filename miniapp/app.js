/* NovelTranslator PRO — Telegram Mini App front-end (vanilla JS, no build step) */
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || "";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = { me: null, config: null, jobs: null, admin: null, tab: "home", timer: null, sheetJob: null };

  // ── Telegram bootstrap ────────────────────────────────────────────────
  if (tg) {
    try { tg.ready(); tg.expand(); } catch (_) {}
    try { tg.setHeaderColor && tg.setHeaderColor("secondary_bg_color"); } catch (_) {}
    try { tg.enableClosingConfirmation && tg.disableClosingConfirmation(); } catch (_) {}
  }
  const haptic = (t) => { try { tg && tg.HapticFeedback && (t === "err" ? tg.HapticFeedback.notificationOccurred("error")
    : t === "ok" ? tg.HapticFeedback.notificationOccurred("success") : tg.HapticFeedback.impactOccurred("light")); } catch (_) {} };

  // ── API helper ────────────────────────────────────────────────────────
  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    if (initData) headers["Authorization"] = "tma " + initData;
    const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok || data.ok === false) {
      const err = new Error(data.error || ("HTTP " + res.status));
      err.code = data.code; err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }

  // ── UI utils ──────────────────────────────────────────────────────────
  function show(view) {
    ["loading", "locked", "auth", "main"].forEach((v) => $("view-" + v).classList.toggle("hidden", v !== view));
  }
  let toastTimer;
  function toast(msg, kind) {
    const t = $("toast"); t.textContent = msg; t.className = "toast " + (kind || "");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
  }
  const fmtInt = (n) => Number(n || 0).toLocaleString();
  const fmtSize = (n) => { n = Number(n || 0); const u = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return (i ? n.toFixed(1) : n.toFixed(0)) + " " + u[i]; };
  const fmtTime = (s) => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`; };
  const fmtAgo = (ts) => { if (!ts) return "—"; const d = Math.floor(Date.now() / 1000 - ts);
    if (d < 60) return "just now"; if (d < 3600) return Math.floor(d / 60) + "m ago"; if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago"; };
  const fmtUptime = (s) => { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`; };
  const langLabel = (c) => { const l = (state.config?.languages || []).find((x) => x.code === c); return l ? `${l.flag} ${l.name}` : c; };
  const fmtLabel = (c) => { const f = (state.config?.formats || []).find((x) => x.code === c); return f ? f.label : (c || "").toUpperCase(); };
  const splitLabel = (kb) => !kb ? "No split" : kb >= 1024 ? (kb / 1024) + " MB" : kb + " KB";

  // ── Render: header + stats ────────────────────────────────────────────
  function renderMe() {
    const me = state.me, cfg = state.config;
    $("me-name").textContent = me.name;
    const role = $("me-role"); role.textContent = me.role; role.className = "badge " + (me.owner ? "owner" : "");
    $("me-sub").textContent = (me.username ? "@" + me.username + " · " : "") + "ID " + me.id;
    if (me.photo) { $("avatar").src = me.photo; $("avatar").classList.remove("hidden"); $("avatar-fallback").classList.add("hidden"); }
    const pill = $("db-pill"); pill.textContent = cfg.db ? "🗄 MongoDB" : "🧠 RAM"; pill.classList.toggle("on", !!cfg.db);
    pill.title = cfg.db ? "Persistent storage: " + cfg.db_name : "In-memory — settings reset on restart";
    $("s-jobs").textContent = fmtInt(me.stats.jobs); $("s-parts").textContent = fmtInt(me.stats.parts); $("s-chars").textContent = fmtInt(me.stats.chars);
    $("nav-admin").classList.toggle("hidden", !me.owner);
    $("pdf-ext").textContent = (cfg.input_exts || []).includes(".pdf") ? " / .pdf" : "";
    $("settings-note").textContent = cfg.db ? "Saved to your profile — used by ⚡ Quick Start." : "⚠️ No database — settings reset when the server restarts.";
    $("split-hint").textContent = `0 = no split · ${cfg.split_range[0]} KB – ${Math.round(cfg.split_range[1] / 1024)} MB`;
    $("setcode-note").textContent = cfg.db ? "Stored in MongoDB — survives restarts." : "RAM only — set SECURITY_CODE env for a permanent code.";
  }

  // ── Render: settings ──────────────────────────────────────────────────
  function renderSettings() {
    const cfg = state.config, p = state.me.prefs;
    $("lang-grid").innerHTML = cfg.languages.map((l) =>
      `<button class="choice ${l.code === p.lang ? "on" : ""}" data-k="lang" data-v="${esc(l.code)}">${l.flag} ${esc(l.name)}</button>`).join("");
    $("fmt-grid").innerHTML = cfg.formats.map((f) =>
      `<button class="choice ${f.code === p.fmt ? "on" : ""}" data-k="fmt" data-v="${esc(f.code)}">${esc(f.label)}</button>`).join("");
    $("split-grid").innerHTML = cfg.split_presets.map((s) =>
      `<button class="choice ${s.kb === p.split ? "on" : ""}" data-k="split" data-v="${s.kb}">${esc(s.label)}</button>`).join("");
    $("split-custom").value = cfg.split_presets.some((s) => s.kb === p.split) ? "" : p.split;
  }
  async function savePref(k, v) {
    haptic();
    try {
      const body = {}; body[k] = k === "split" ? Number(v) : v;
      const r = await api("/api/settings", { method: "POST", body });
      state.me = r.me; renderSettings(); toast("✅ Saved", "ok");
    } catch (e) { toast(e.message, "err"); haptic("err"); }
  }
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest(".choice"); if (b && b.dataset.k) savePref(b.dataset.k, b.dataset.v);
  });
  $("split-apply").onclick = () => { const v = $("split-custom").value; if (v !== "") savePref("split", v); };

  // ── Render: jobs ──────────────────────────────────────────────────────
  function jobRow(j, opts = {}) {
    const p = j.progress || {};
    const pct = Math.round((p.ratio || 0) * 100);
    const running = j.status === "running";
    return `<div class="job" data-id="${esc(j.job_id)}">
      <div class="job-head"><div class="job-name">${esc(j.name)}</div>
        <span class="status ${esc(j.status)}">${running ? "▶ running" : j.status === "queued" ? "#" + j.position + " queued" : esc(j.status)}</span></div>
      <div class="job-meta">${langLabel(j.lang)} · ${fmtLabel(j.fmt)} · ${splitLabel(j.split)} · ${fmtSize(j.size)}${j.user && !j.mine ? " · 👤 " + esc(j.user) : ""}</div>
      ${running ? `<div class="progress"><i style="width:${pct}%"></i></div>
        <div class="progress-meta"><span>${pct}% · part ${p.part || 0}/${p.parts || "?"} · ${p.chunks_done || 0}/${p.chunks_total || "?"} chunks</span>
        <span>${p.speed ? p.speed + " c/s · " : ""}ETA ${fmtTime(p.eta)}</span></div>
        <div class="tiny muted">${esc(p.phase || "")} · elapsed ${fmtTime(p.elapsed)}</div>` : ""}
      ${(j.mine || state.me.owner) && (running || j.status === "queued") && !opts.noActions
        ? `<div class="job-actions"><button class="btn small danger" data-cancel="${esc(j.job_id)}">🛑 Cancel</button></div>` : ""}
    </div>`;
  }
  function renderJobs() {
    const d = state.jobs; if (!d) return;
    $("alert-shutdown").classList.toggle("hidden", !d.shutting_down);
    $("active-box").innerHTML = d.active ? jobRow(d.active) : `<p class="muted small">Nothing is running right now.</p>`;
    $("queue-count").textContent = d.queue_len;
    $("queue-list").innerHTML = d.queue.length ? d.queue.map((j) => jobRow(j)).join("") : `<p class="muted small">Queue is empty.</p>`;
    const pend = d.pending || [];
    $("pending-card").classList.toggle("hidden", !pend.length);
    $("pending-list").innerHTML = pend.map((j) => `<div class="job">
        <div class="job-head"><div class="job-name">${esc(j.name)}</div><span class="status">waiting for options</span></div>
        <div class="job-meta">${fmtSize(j.size)} · ${esc(j.ext)}</div>
        <div class="job-actions"><button class="btn small primary" data-config="${esc(j.job_id)}">⚙️ Configure &amp; start</button>
        <button class="btn small danger" data-cancel="${esc(j.job_id)}">Discard</button></div></div>`).join("");
    const hist = d.history || [];
    $("history-list").innerHTML = hist.length ? hist.map(histRow).join("") : `<p class="muted small">No history yet.</p>`;
  }
  function histRow(h) {
    return `<div class="job"><div class="job-head"><div class="job-name">${esc(h.name)}</div><span class="status ${esc(h.status)}">${esc(h.status)}</span></div>
      <div class="job-meta">${langLabel(h.lang)} · ${fmtLabel(h.fmt)} · ${fmtSize(h.size)}${h.parts ? " · " + h.parts + " parts" : ""}${h.chars ? " · " + fmtInt(h.chars) + " chars" : ""}${h.secs ? " · " + fmtTime(h.secs) : ""}${h.error ? " · " + esc(h.error) : ""}${h.user && state.me.owner ? " · 👤 " + esc(h.user) : ""} · ${fmtAgo(h.ts)}</div></div>`;
  }
  document.addEventListener("click", async (ev) => {
    const c = ev.target.closest("[data-cancel]");
    if (c) {
      haptic();
      const ok = tg && tg.showConfirm ? await new Promise((r) => tg.showConfirm("Cancel this job?", r)) : confirm("Cancel this job?");
      if (!ok) return;
      try { await api("/api/jobs/cancel", { method: "POST", body: { job_id: c.dataset.cancel } }); toast("🛑 Cancelled"); refreshJobs(); }
      catch (e) { toast(e.message, "err"); }
    }
    const cfgBtn = ev.target.closest("[data-config]");
    if (cfgBtn) openSheet(cfgBtn.dataset.config);
  });

  // ── Job configuration sheet ───────────────────────────────────────────
  function openSheet(jobId) {
    const j = (state.jobs?.pending || []).find((x) => x.job_id === jobId);
    if (!j) { toast("Upload not found — send the file again.", "err"); return; }
    state.sheetJob = j;
    const cfg = state.config, p = state.me.prefs;
    $("sheet-file").textContent = `${j.name} · ${fmtSize(j.size)}`;
    $("sheet-lang").innerHTML = cfg.languages.map((l) => `<option value="${esc(l.code)}" ${l.code === (j.lang || p.lang) ? "selected" : ""}>${l.flag} ${esc(l.name)}</option>`).join("");
    $("sheet-fmt").innerHTML = cfg.formats.map((f) => `<option value="${esc(f.code)}" ${f.code === (j.fmt || p.fmt) ? "selected" : ""}>${esc(f.label)}</option>`).join("");
    const split = j.split ?? p.split;
    const presets = cfg.split_presets.slice();
    if (!presets.some((s) => s.kb === split)) presets.push({ kb: split, label: splitLabel(split) });
    $("sheet-split").innerHTML = presets.map((s) => `<option value="${s.kb}" ${s.kb === split ? "selected" : ""}>${esc(s.label)}</option>`).join("");
    $("sheet").classList.remove("hidden");
    if (tg && tg.BackButton) { tg.BackButton.show(); tg.BackButton.onClick(closeSheet); }
  }
  function closeSheet() {
    $("sheet").classList.add("hidden"); state.sheetJob = null;
    if (tg && tg.BackButton) { tg.BackButton.hide(); tg.BackButton.offClick(closeSheet); }
    if (location.hash) history.replaceState(null, "", location.pathname);
  }
  $("sheet-close").onclick = closeSheet;
  $("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
  $("sheet-start").onclick = async () => {
    if (!state.sheetJob) return; haptic();
    $("sheet-start").disabled = true;
    try {
      await api("/api/jobs/start", { method: "POST", body: { job_id: state.sheetJob.job_id,
        lang: $("sheet-lang").value, fmt: $("sheet-fmt").value, split: Number($("sheet-split").value) } });
      toast("🚀 Job queued", "ok"); haptic("ok"); closeSheet(); refreshJobs();
    } catch (e) { toast(e.message, "err"); haptic("err"); }
    finally { $("sheet-start").disabled = false; }
  };
  $("sheet-cancel").onclick = async () => {
    if (!state.sheetJob) return;
    try { await api("/api/jobs/cancel", { method: "POST", body: { job_id: state.sheetJob.job_id } }); toast("Upload discarded"); closeSheet(); refreshJobs(); }
    catch (e) { toast(e.message, "err"); }
  };

  // ── Admin ─────────────────────────────────────────────────────────────
  async function refreshAdmin() {
    if (!state.me?.owner) return;
    try {
      const a = await api("/api/admin/overview"); state.admin = a;
      $("a-users").textContent = fmtInt(a.users.length); $("a-jobs").textContent = fmtInt(a.stats.jobs);
      $("a-uptime").textContent = fmtUptime(a.uptime); $("a-parts").textContent = fmtInt(a.stats.parts);
      $("a-failed").textContent = fmtInt(a.stats.failed); $("a-cancel").textContent = fmtInt(a.stats.cancelled);
      $("admin-users").innerHTML = a.users.map((u) => `<div class="user">
          <div><div class="u-name">${esc(u.name)} ${u.role === "owner" ? "👑" : ""}</div>
          <div class="u-meta">ID ${u.id} · ${langLabel(u.lang)} · ${fmtInt(u.stats?.jobs || 0)} jobs · seen ${fmtAgo(u.last_seen)}</div></div>
          ${u.role !== "owner" ? `<button class="btn small danger" data-deluser="${u.id}">Remove</button>` : ""}</div>`).join("") || `<p class="muted small">No users.</p>`;
      $("admin-recent").innerHTML = (a.recent || []).length ? a.recent.map(histRow).join("") : `<p class="muted small">Nothing yet.</p>`;
    } catch (e) { toast(e.message, "err"); }
  }
  document.addEventListener("click", async (ev) => {
    const d = ev.target.closest("[data-deluser]"); if (!d) return;
    const ok = tg && tg.showConfirm ? await new Promise((r) => tg.showConfirm("Remove this user?", r)) : confirm("Remove this user?");
    if (!ok) return;
    try { await api("/api/admin/users", { method: "POST", body: { action: "remove", id: Number(d.dataset.deluser) } }); toast("Removed"); refreshAdmin(); }
    catch (e) { toast(e.message, "err"); }
  });
  $("adduser-form").onsubmit = async (e) => {
    e.preventDefault(); const id = Number($("adduser-id").value); if (!id) return;
    try { await api("/api/admin/users", { method: "POST", body: { action: "add", id } }); $("adduser-id").value = ""; toast("✅ User added", "ok"); refreshAdmin(); }
    catch (er) { toast(er.message, "err"); }
  };
  $("bc-send").onclick = async () => {
    const text = $("bc-text").value.trim(); if (!text) return;
    const ok = tg && tg.showConfirm ? await new Promise((r) => tg.showConfirm("Send to all users?", r)) : confirm("Send to all users?");
    if (!ok) return;
    $("bc-send").disabled = true;
    try { const r = await api("/api/admin/broadcast", { method: "POST", body: { text } }); $("bc-text").value = ""; toast(`📣 Sending to ${r.recipients} users`, "ok"); }
    catch (e) { toast(e.message, "err"); } finally { $("bc-send").disabled = false; }
  };
  $("setcode-form").onsubmit = async (e) => {
    e.preventDefault();
    try { const r = await api("/api/admin/setcode", { method: "POST", body: { code: $("setcode-val").value } });
      $("setcode-val").value = ""; toast(r.persistent ? "🔐 Code saved (persistent)" : "🔐 Code set (until restart)", "ok"); }
    catch (er) { toast(er.message, "err"); }
  };

  // ── Tabs ──────────────────────────────────────────────────────────────
  function setTab(name) {
    state.tab = name;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + name));
    document.querySelectorAll(".nav").forEach((n) => n.classList.toggle("active", n.dataset.tab === name));
    if (name === "admin") refreshAdmin();
    window.scrollTo({ top: 0 });
  }
  document.querySelectorAll(".nav").forEach((n) => n.onclick = () => { haptic(); setTab(n.dataset.tab); });

  // ── Polling ───────────────────────────────────────────────────────────
  async function refreshJobs() {
    try { state.jobs = await api("/api/jobs"); renderJobs(); }
    catch (e) { if (e.code === "locked") showLocked(e.data); }
    const hasLive = state.jobs && (state.jobs.active || state.jobs.queue.length);
    clearTimeout(state.timer);
    state.timer = setTimeout(refreshJobs, hasLive ? 2500 : 8000);
  }
  async function refreshMe() {
    try { const r = await api("/api/me"); state.me = r.me; state.config = r.config; renderMe(); renderSettings(); } catch (_) {}
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.me) { refreshJobs(); refreshMe(); } });

  // ── Lock / unlock ─────────────────────────────────────────────────────
  function showLocked(data) {
    $("locked-id").textContent = (data && data.id) || "—"; show("locked");
    setTimeout(() => $("unlock-code").focus(), 100);
  }
  $("unlock-form").onsubmit = async (e) => {
    e.preventDefault(); $("unlock-error").classList.add("hidden");
    const btn = e.target.querySelector("button"); btn.disabled = true;
    try { const r = await api("/api/unlock", { method: "POST", body: { code: $("unlock-code").value.trim() } }); haptic("ok"); boot(r); }
    catch (er) { $("unlock-error").textContent = er.message; $("unlock-error").classList.remove("hidden"); haptic("err"); }
    finally { btn.disabled = false; }
  };

  // ── Boot ──────────────────────────────────────────────────────────────
  function boot(r) {
    state.me = r.me; state.config = r.config;
    renderMe(); renderSettings(); show("main");
    refreshJobs().then(() => {
      const m = location.hash.match(/job=([A-Za-z0-9_-]+)/);
      if (m) openSheet(m[1]);
    });
    if (state.me.owner) refreshAdmin();
  }
  async function start() {
    try { boot(await api("/api/me")); }
    catch (e) {
      if (e.code === "locked") return showLocked(e.data);
      if (e.code === "auth" || e.status === 401) {
        show("auth");
        try { const h = await fetch("/health").then((x) => x.json()); if (h.bot) { $("auth-link").href = "https://t.me/" + h.bot; $("auth-link").classList.remove("hidden"); } } catch (_) {}
        return;
      }
      show("auth"); toast(e.message, "err");
    }
  }
  start();
})();
