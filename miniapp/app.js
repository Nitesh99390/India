/* NovelTranslator PRO — Telegram Mini App front-end (vanilla JS, no build step)
   v5: approval-based access (request / pending / approved / expired / rejected / banned) */
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || "";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = { me: null, config: null, jobs: null, admin: null, tab: "home", timer: null, sheetJob: null,
                  filter: "all", sheetUser: null, lockedTimer: null, refreshing: false,
                  pollFails: 0, lastPoll: 0, lastMe: 0, booted: false };
  const TAB_ORDER = ["home", "settings", "history", "admin"];
  const POLL_LIVE = 2500, POLL_IDLE = 8000, POLL_MAX_BACKOFF = 30000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ── Telegram bootstrap ────────────────────────────────────────────────
  if (tg) {
    try { tg.ready(); tg.expand(); } catch (_) {}
    try { tg.setHeaderColor && tg.setHeaderColor("secondary_bg_color"); } catch (_) {}
    try { tg.disableClosingConfirmation && tg.disableClosingConfirmation(); } catch (_) {}
    // Our own pull-to-refresh gesture must not collapse/close the Mini App (Bot API 7.7+)
    try { tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (_) {}
  }

  // ── Haptic feedback ──
  //   haptic()            light tap (default)      haptic("medium"|"heavy"|"rigid"|"soft")
  //   haptic("select")    selection changed        haptic("ok") / haptic("warn") / haptic("err")
  // Falls back to navigator.vibrate() when opened outside Telegram (Android browsers).
  const HF = tg && tg.HapticFeedback;
  let lastHaptic = 0;
  function haptic(t) {
    const now = Date.now(); if (now - lastHaptic < 35) return; lastHaptic = now;     // debounce bursts
    try {
      if (HF) {
        if (t === "ok" || t === "err" || t === "warn") HF.notificationOccurred({ ok: "success", err: "error", warn: "warning" }[t]);
        else if (t === "select") HF.selectionChanged();
        else HF.impactOccurred(t && ["light", "medium", "heavy", "rigid", "soft"].includes(t) ? t : "light");
      } else if (navigator.vibrate) {
        navigator.vibrate(t === "err" ? [30, 40, 30] : t === "ok" ? [10, 30, 20] : t === "heavy" || t === "medium" ? 18 : 8);
      }
    } catch (_) {}
  }
  // Every tappable control gives a tiny tick the moment the finger lands on it
  document.addEventListener("pointerdown", (ev) => {
    const el = ev.target.closest(".btn, .choice, .nav, .iconbtn, .x, .user, .filters button, select");
    if (!el || el.disabled) return;
    haptic(el.classList.contains("choice") || el.classList.contains("nav") ? "select" : el.classList.contains("danger") ? "medium" : "light");
  }, { passive: true });
  const ask = (msg) => { haptic("warn"); return tg && tg.showConfirm ? new Promise((r) => tg.showConfirm(msg, r)) : Promise.resolve(confirm(msg)); };

  // ── API helper ────────────────────────────────────────────────────────
  // Every request has a hard timeout (a hung request must never leave the UI
  // stuck on the skeleton). Network failures / timeouts are flagged with
  // `err.network = true` so callers can retry quietly instead of showing a
  // misleading error screen.
  async function api(path, opts = {}) {
    const { timeout = 20000, body, ...rest } = opts;
    const headers = { "Content-Type": "application/json" };
    if (initData) headers["Authorization"] = "tma " + initData;
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const tid = ctl ? setTimeout(() => ctl.abort(), timeout) : 0;
    let res;
    try {
      res = await fetch(path, { ...rest, headers, cache: "no-store", signal: ctl ? ctl.signal : undefined,
                                body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      const err = new Error(e && e.name === "AbortError" ? "Request timed out" : "Network error");
      err.network = true; err.status = 0; err.data = {};
      throw err;
    } finally { clearTimeout(tid); }
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok || data.ok === false) {
      const err = new Error(data.error || ("HTTP " + res.status));
      err.code = data.code; err.status = res.status; err.data = data;
      // Reverse-proxy answers while the backend is (re)starting → treat like a network hiccup
      err.network = !data.error && [502, 503, 504].includes(res.status);
      throw err;
    }
    return data;
  }

  // ── DOM morphing ─────────────────────────────────────────────────────
  // Background polls must never *look* like a page reload. Instead of
  // `el.innerHTML = html` (which throws away every node → entrance animations
  // replay, progress bars jump, taps get lost) we diff the new markup against
  // the live DOM and patch only what changed. Rows are matched by key
  // (data-id / data-user) so reordering keeps the same nodes.
  const keyOf = (n) => n.nodeType === 1 ? (n.getAttribute("data-id") || n.getAttribute("data-user") || n.getAttribute("data-key") || "") : "";
  function morphAttrs(from, to) {
    for (const a of Array.from(from.attributes)) if (!to.hasAttribute(a.name)) from.removeAttribute(a.name);
    for (const a of Array.from(to.attributes)) if (from.getAttribute(a.name) !== a.value) from.setAttribute(a.name, a.value);
  }
  function morphChildren(from, to) {
    const tc = Array.from(to.childNodes);
    for (let i = 0; i < tc.length; i++) {
      const t = tc[i], cur = from.childNodes[i] || null, k = keyOf(t);
      let match = null;
      if (cur && cur.nodeType === t.nodeType && (t.nodeType !== 1 || (cur.tagName === t.tagName && keyOf(cur) === k))) match = cur;
      else if (k) for (let j = i + 1; j < from.childNodes.length; j++) {
        const c = from.childNodes[j];
        if (c.nodeType === 1 && c.tagName === t.tagName && keyOf(c) === k) { match = c; break; }
      }
      if (!match) { from.insertBefore(t.cloneNode(true), cur); continue; }
      if (match !== cur) from.insertBefore(match, cur);
      if (t.nodeType === 3 || t.nodeType === 8) { if (match.nodeValue !== t.nodeValue) match.nodeValue = t.nodeValue; }
      else morph(match, t);
    }
    while (from.childNodes.length > tc.length) from.removeChild(from.lastChild);
  }
  function morph(from, to) {
    morphAttrs(from, to);
    // never rewrite what the user may be typing in / has selected
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(from.tagName)) return;
    morphChildren(from, to);
  }
  // Set a container's markup: first paint replaces the skeleton outright,
  // later paints are diffed. No-op when the markup did not change at all.
  function setHTML(el, html) {
    if (typeof el === "string") el = $(el);
    if (!el || el._html === html) return;
    const first = el._html === undefined;
    el._html = html;
    if (first || !el.firstChild) { el.innerHTML = html; el.classList.remove("skel-host"); return; }
    const tmp = document.createElement(el.tagName); tmp.innerHTML = html;
    morphChildren(el, tmp);
  }
  function setText(el, s) {
    if (typeof el === "string") el = $(el);
    if (el && el.textContent !== String(s)) el.textContent = s;
  }

  // ── UI utils ──────────────────────────────────────────────────────────
  // Re-run an entrance animation on an element by toggling a class
  function animate(el, cls, ms = 600) {
    if (!el) return; el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
    clearTimeout(el._animT); el._animT = setTimeout(() => el.classList.remove(cls), ms);
  }
  function show(view) {
    const cur = ["loading", "locked", "auth", "main"].find((v) => !$("view-" + v).classList.contains("hidden"));
    if (cur === view) return;
    ["loading", "locked", "auth", "main"].forEach((v) => $("view-" + v).classList.toggle("hidden", v !== view));
    animate($("view-" + view), "enter", 900);
    window.scrollTo({ top: 0 });
  }
  let toastTimer, toastOutTimer;
  function toast(msg, kind) {
    const t = $("toast"); t.textContent = msg; t.className = "toast " + (kind || "");
    void t.offsetWidth;                                   // restart the pop-in animation
    clearTimeout(toastTimer); clearTimeout(toastOutTimer);
    toastTimer = setTimeout(() => { t.classList.add("out"); toastOutTimer = setTimeout(() => t.classList.add("hidden"), 240); }, 2400);
  }
  // Update a number and "bump" it when the value actually changed
  function setNum(id, val) {
    const el = $(id); if (!el) return; const s = String(val);
    if (el.textContent !== s) { el.textContent = s; if (el.dataset.ready) animate(el, "bump", 500); el.dataset.ready = "1"; }
  }
  const fmtInt = (n) => Number(n || 0).toLocaleString();
  const fmtSize = (n) => { n = Number(n || 0); const u = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return (i ? n.toFixed(1) : n.toFixed(0)) + " " + u[i]; };
  const fmtTime = (s) => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`; };
  const fmtAgo = (ts) => { if (!ts) return "—"; const d = Math.floor(Date.now() / 1000 - ts);
    if (d < 60) return "just now"; if (d < 3600) return Math.floor(d / 60) + "m ago"; if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago"; };
  const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—";
  const fmtDur = (s) => { s = Math.max(0, Math.floor(s || 0)); if (s < 3600) return Math.ceil(s / 60) + " min"; if (s < 86400) return Math.ceil(s / 3600) + " h";
    return Math.ceil(s / 86400) + " d"; };
  const fmtUptime = (s) => { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`; };
  const langLabel = (c) => { const l = (state.config?.languages || []).find((x) => x.code === c); return l ? `${l.flag} ${l.name}` : c; };
  const fmtLabel = (c) => { const f = (state.config?.formats || []).find((x) => x.code === c); return f ? f.label : (c || "").toUpperCase(); };
  const splitLabel = (kb) => !kb ? "No split" : kb >= 1024 ? (kb / 1024) + " MB" : kb + " KB";
  const stChip = (a) => `<span class="st ${esc(a.status)}">${a.icon} ${esc(a.label)}</span>`;
  const isAdmin = () => !!(state.me && (state.me.owner || state.me.admin));
  const isOwner = () => !!(state.me && state.me.owner);

  // ── Render: header + stats ────────────────────────────────────────────
  function renderMe() {
    const me = state.me, cfg = state.config;
    setText("me-name", me.name);
    const role = $("me-role"); setText(role, me.owner ? "owner" : me.admin ? "admin" : me.role);
    const roleCls = "badge " + (me.owner ? "owner" : me.admin ? "admin" : "");
    if (role.className !== roleCls) role.className = roleCls;
    setText("me-sub", (me.username ? "@" + me.username + " · " : "") + "ID " + me.id);
    if (me.photo) {
      const av = $("avatar");
      if (av.getAttribute("src") !== me.photo) av.src = me.photo;          // don't re-download on every poll
      av.classList.remove("hidden"); $("avatar-fallback").classList.add("hidden");
    }
    const pill = $("db-pill"); setText(pill, cfg.db ? "🗄 MongoDB" : "🧠 RAM"); pill.classList.toggle("on", !!cfg.db);
    pill.title = cfg.db ? "Persistent storage: " + cfg.db_name : "In-memory — settings reset on restart";
    setNum("s-jobs", fmtInt(me.stats.jobs)); setNum("s-parts", fmtInt(me.stats.parts)); setNum("s-chars", fmtInt(me.stats.chars));
    $("nav-admin").classList.toggle("hidden", !isAdmin());
    document.querySelectorAll(".owner-only").forEach((el) => el.classList.toggle("hidden", !me.owner));
    setText("pdf-ext", (cfg.input_exts || []).includes(".pdf") ? " / .pdf" : "");
    setText("settings-note", cfg.db ? "Saved to your profile — used by ⚡ Quick Start." : "⚠️ No database — settings reset when the server restarts.");
    setText("split-hint", `0 = no split · ${cfg.split_range[0]} KB – ${Math.round(cfg.split_range[1] / 1024)} MB`);
    renderAccess();
  }

  // ── Render: my access card (home tab) ─────────────────────────────────
  function renderAccess() {
    const me = state.me, a = me.access || {}, cfg = state.config || {};
    setHTML("access-chip", me.owner ? "👑 Owner" : `${a.icon || ""} ${esc(a.label || "")}`);
    const rows = [];
    if (me.owner) {
      rows.push(["Role", "👑 Owner · full access"]);
    } else {
      if (me.admin) rows.push(["Role", "🛡 Admin"]);
      rows.push(["Validity", a.lifetime ? "♾ Lifetime" : (a.expiry_label || "—")]);
      if (a.expires) rows.push(["Expires on", fmtDate(a.expires)]);
      if (a.plan_label) rows.push(["Plan", a.plan_label]);
      if (a.approved_at) rows.push(["Approved", fmtDate(a.approved_at)]);
    }
    if (cfg.public_mode) rows.push(["Mode", "🌐 Public bot"]);
    setHTML("access-body", rows.map(([k, v]) =>
      `<div class="access-row"><span class="muted">${esc(k)}</span><b>${esc(v)}</b></div>`).join(""));
    // Banner: expiring soon
    const banner = $("access-banner");
    if (!me.owner && a.expires && a.days_left != null && a.days_left <= (cfg.reminder_days || 3)) {
      banner.classList.remove("hidden");
      setText(banner, a.days_left <= 0 ? "⌛ Your access expires today. Ask the owner to extend it."
        : `⏳ Your access expires in ${a.days_left} day${a.days_left === 1 ? "" : "s"} (${fmtDate(a.expires)}).`);
    } else { banner.classList.add("hidden"); }
  }

  // ── Render: settings ──────────────────────────────────────────────────
  function renderSettings() {
    const cfg = state.config, p = state.me.prefs;
    setHTML("lang-grid", cfg.languages.map((l) =>
      `<button class="choice ${l.code === p.lang ? "on" : ""}" data-k="lang" data-key="${esc(l.code)}" data-v="${esc(l.code)}">${l.flag} ${esc(l.name)}</button>`).join(""));
    setHTML("fmt-grid", cfg.formats.map((f) =>
      `<button class="choice ${f.code === p.fmt ? "on" : ""}" data-k="fmt" data-key="${esc(f.code)}" data-v="${esc(f.code)}">${esc(f.label)}</button>`).join(""));
    setHTML("split-grid", cfg.split_presets.map((s) =>
      `<button class="choice ${s.kb === p.split ? "on" : ""}" data-k="split" data-key="${s.kb}" data-v="${s.kb}">${esc(s.label)}</button>`).join(""));
    const custom = $("split-custom"), want = cfg.split_presets.some((s) => s.kb === p.split) ? "" : String(p.split);
    if (document.activeElement !== custom && custom.value !== want) custom.value = want;   // don't clobber typing
  }
  let savingPref = false;
  async function savePref(k, v) {
    if (savingPref) return; savingPref = true;
    // optimistic highlight — the UI answers the tap instantly, the server confirms
    document.querySelectorAll(`.choice[data-k="${k}"]`).forEach((b) => b.classList.toggle("on", b.dataset.v === String(v)));
    try {
      const body = {}; body[k] = k === "split" ? Number(v) : v;
      const r = await api("/api/settings", { method: "POST", body });
      state.me = r.me; renderSettings(); toast("✅ Saved", "ok"); haptic("ok");
      const on = document.querySelector(`.choice.on[data-k="${k}"]`); if (on) animate(on, "just-on", 400);
    } catch (e) { renderSettings(); toast(e.message, "err"); haptic("err"); }
    finally { savingPref = false; }
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
      ${(j.mine || isOwner()) && (running || j.status === "queued") && !opts.noActions
        ? `<div class="job-actions"><button class="btn small danger" data-cancel="${esc(j.job_id)}">🛑 Cancel</button></div>` : ""}
    </div>`;
  }
  function renderJobs() {
    const d = state.jobs; if (!d) return;
    $("alert-shutdown").classList.toggle("hidden", !d.shutting_down);
    setHTML("active-box", d.active ? jobRow(d.active) : `<p class="muted small">Nothing is running right now.</p>`);
    setNum("queue-count", d.queue_len);
    setHTML("queue-list", d.queue.length ? d.queue.map((j) => jobRow(j)).join("") : `<p class="muted small">Queue is empty.</p>`);
    const pend = d.pending || [];
    $("pending-card").classList.toggle("hidden", !pend.length);
    setHTML("pending-list", pend.map((j) => `<div class="job" data-id="${esc(j.job_id)}">
        <div class="job-head"><div class="job-name">${esc(j.name)}</div><span class="status">waiting for options</span></div>
        <div class="job-meta">${fmtSize(j.size)} · ${esc(j.ext)}</div>
        <div class="job-actions"><button class="btn small primary" data-config="${esc(j.job_id)}">⚙️ Configure &amp; start</button>
        <button class="btn small danger" data-cancel="${esc(j.job_id)}">Discard</button></div></div>`).join(""));
    const hist = d.history || [];
    setHTML("history-list", hist.length ? hist.map(histRow).join("") : `<p class="muted small">No history yet.</p>`);
  }
  function histRow(h) {
    return `<div class="job" data-id="${esc(h.job_id || (h.name + "|" + h.ts))}"><div class="job-head"><div class="job-name">${esc(h.name)}</div><span class="status ${esc(h.status)}">${esc(h.status)}</span></div>
      <div class="job-meta">${langLabel(h.lang)} · ${fmtLabel(h.fmt)} · ${fmtSize(h.size)}${h.parts ? " · " + h.parts + " parts" : ""}${h.chars ? " · " + fmtInt(h.chars) + " chars" : ""}${h.secs ? " · " + fmtTime(h.secs) : ""}${h.error ? " · " + esc(h.error) : ""}${h.user && isOwner() ? " · 👤 " + esc(h.user) : ""} · ${fmtAgo(h.ts)}</div></div>`;
  }
  document.addEventListener("click", async (ev) => {
    const c = ev.target.closest("[data-cancel]");
    if (c) {
      if (!(await ask("Cancel this job?"))) return;
      c.disabled = true;
      try { await api("/api/jobs/cancel", { method: "POST", body: { job_id: c.dataset.cancel } }); toast("🛑 Cancelled"); haptic("ok"); refreshJobs(true); }
      catch (e) { c.disabled = false; toast(e.message, "err"); haptic("err"); }
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
    openSheetEl("sheet");
    if (tg && tg.BackButton) { tg.BackButton.show(); tg.BackButton.onClick(closeSheet); }
  }
  // Bottom sheets: animated open / close (slide up / slide down)
  function openSheetEl(id) { const s = $(id); s.classList.remove("closing"); s.classList.remove("hidden"); haptic("medium"); }
  function closeSheetEl(id) {
    const s = $(id); if (s.classList.contains("hidden")) return;
    s.classList.add("closing"); haptic("soft");
    setTimeout(() => { s.classList.add("hidden"); s.classList.remove("closing"); }, 220);
  }
  function closeSheet() {
    closeSheetEl("sheet"); state.sheetJob = null;
    if (tg && tg.BackButton) { tg.BackButton.hide(); tg.BackButton.offClick(closeSheet); }
    if (location.hash) history.replaceState(null, "", location.pathname);
  }
  $("sheet-close").onclick = closeSheet;
  $("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
  $("sheet-start").onclick = async () => {
    if (!state.sheetJob) return;
    $("sheet-start").disabled = true;
    try {
      await api("/api/jobs/start", { method: "POST", body: { job_id: state.sheetJob.job_id,
        lang: $("sheet-lang").value, fmt: $("sheet-fmt").value, split: Number($("sheet-split").value) } });
      toast("🚀 Job queued", "ok"); haptic("ok"); closeSheet(); refreshJobs(true);
    } catch (e) { toast(e.message, "err"); haptic("err"); }
    finally { $("sheet-start").disabled = false; }
  };
  $("sheet-cancel").onclick = async () => {
    if (!state.sheetJob) return;
    $("sheet-cancel").disabled = true;
    try { await api("/api/jobs/cancel", { method: "POST", body: { job_id: state.sheetJob.job_id } }); toast("Upload discarded"); haptic("ok"); closeSheet(); refreshJobs(true); }
    catch (e) { toast(e.message, "err"); haptic("err"); }
    finally { $("sheet-cancel").disabled = false; }
  };

  // ── Admin: overview ───────────────────────────────────────────────────
  function userMatchesFilter(u) {
    const f = state.filter, st = u.access?.status;
    if (f === "all") return true;
    if (f === "admin") return u.role === "admin" || u.role === "owner";
    return st === f;
  }
  function userRow(u) {
    const a = u.access || {};
    const roleIcon = u.role === "owner" ? " 👑" : u.role === "admin" ? " 🛡" : "";
    const exp = u.role === "owner" ? "" : a.lifetime ? "♾ lifetime" : a.expires ? (a.days_left <= 0 ? "expires today" : `${a.days_left} d left`) : "";
    return `<div class="user" data-user="${u.id}">
        <div><div class="u-name">${esc(u.name)}${roleIcon}${u.username ? ` <span class="muted tiny">@${esc(u.username)}</span>` : ""}</div>
        <div class="u-meta">ID ${u.id} · ${fmtInt(u.stats?.jobs || 0)} jobs · seen ${fmtAgo(u.last_seen)}${u.env ? " · env" : ""}</div></div>
        <div class="u-right">${u.role === "owner" ? `<span class="st approved">👑 Owner</span>` : stChip(a)}${exp ? `<span class="u-exp">${esc(exp)}</span>` : ""}</div>
      </div>`;
  }
  function reqRow(u) {
    const a = u.access || {};
    const plans = (state.config?.plans || []).slice(0, 4);
    return `<div class="req" data-user="${u.id}">
        <div class="u-name">${esc(u.name)}${u.username ? ` <span class="muted tiny">@${esc(u.username)}</span>` : ""} <span class="muted tiny">· ID ${u.id}</span></div>
        <div class="u-meta">Requested ${fmtAgo(a.requested_at)} · ${a.requests || 1} request${(a.requests || 1) === 1 ? "" : "s"} · joined ${fmtDate(u.joined)}</div>
        ${a.note ? `<div class="req-note">“${esc(a.note)}”</div>` : ""}
        <div class="req-actions">
          ${plans.map((p) => `<button class="btn ok" data-act="approve" data-uid="${u.id}" data-dur="${esc(p.code)}">✅ ${esc(p.label)}</button>`).join("")}
          <button class="btn" data-open-user="${u.id}">⚙️ More</button>
          <button class="btn danger" data-act="reject" data-uid="${u.id}">❌ Reject</button>
        </div>
      </div>`;
  }
  function auditRow(e) {
    const who = e.actor ? esc(e.actor_name || e.actor) : "system";
    const tgt = e.target ? esc(e.target_name || e.target) : "";
    const extra = [e.plan ? esc(e.plan) : "", e.expires === 0 && /approve|extend/.test(e.action) ? "♾" : e.expires ? "→ " + fmtDate(e.expires) : "",
      e.reason ? "“" + esc(e.reason) + "”" : ""].filter(Boolean).join(" · ");
    return `<div class="audit" data-key="${esc((e.ts || "") + "|" + (e.action || "") + "|" + (e.target || ""))}"><b>${who}</b> ${esc(e.action)}${tgt ? " → <b>" + tgt + "</b>" : ""}${extra ? " · " + extra : ""} <span class="tiny">· ${fmtAgo(e.ts)}</span></div>`;
  }
  function renderAdmin() {
    const a = state.admin; if (!a) return;
    setNum("a-users", fmtInt(a.users.length)); setNum("a-jobs", fmtInt(a.stats.jobs));
    setNum("a-uptime", fmtUptime(a.uptime)); setNum("a-parts", fmtInt(a.stats.parts));
    setNum("a-failed", fmtInt(a.stats.failed)); setNum("a-cancel", fmtInt(a.stats.cancelled));
    const bud = a.db_budget; $("db-card").classList.toggle("hidden", !bud);
    if (bud) {
      const pct = Math.min(100, bud.percent || 0);
      $("db-bar").style.width = pct + "%"; $("db-bar").style.background = pct > 85 ? "var(--danger, #ef4444)" : "";
      setText("db-size", `${(bud.size_mb || 0).toFixed(1)} / ${bud.budget_mb} MB (${pct}%)`);
      setText("db-docs", `${fmtInt(bud.job_docs)} / ${fmtInt(bud.max_job_docs)} job docs · TTL ${bud.ttl_days} d${bud.pruned ? " · pruned " + fmtInt(bud.pruned) : ""}`);
    }
    const workers = a.workers || [];
    const busy = workers.filter((w) => w.status === "running").length;
    setText("worker-count", `${a.workers_online || 0} online${busy ? ` · ${busy} busy` : ""}`);
    setHTML("worker-nodes", workers.length ? workers.map((worker) => {
      const lastSeen = worker.last_seen ? fmtAgo(worker.last_seen) : "never";
      const current = worker.current_job ? `Translating ${esc(worker.current_job)}` : "Waiting for a job";
      const kind = worker.embedded ? " <span class=\"chip tiny\">embedded</span>" : "";
      const done = (worker.jobs_done || worker.jobs_failed)
        ? ` · ✅ ${fmtInt(worker.jobs_done || 0)}${worker.jobs_failed ? ` · ⚠️ ${fmtInt(worker.jobs_failed)}` : ""}` : "";
      return `<div class="worker-node" data-key="${esc(worker.node_id || "")}"><span class="worker-dot ${worker.status === "idle" ? "idle" : "on"}"></span><div><b>${esc(worker.node_id || "Worker")}</b>${kind}<div class="tiny muted">${esc(current)} · heartbeat ${esc(lastSeen)}${done}</div></div><span class="st ${worker.status === "idle" ? "approved" : "pending"}">${esc(worker.status || "unknown")}</span></div>`;
    }).join("") : `<p class="muted small">No active worker nodes. The master runs an embedded worker when MongoDB is connected (EMBEDDED_WORKER=1); start extra Render worker services for more capacity.</p>`);
    const pending = a.pending || [];
    setNum("a-pending", pending.length);
    setHTML("admin-pending", pending.length ? pending.map(reqRow).join("") : `<p class="muted small">No pending requests. 🎉</p>`);
    const c = a.counts || {};
    setText("a-counts", `✅ ${c.approved || 0} · ⏳ ${c.pending || 0} · ⌛ ${c.expired || 0}`);
    const list = a.users.filter(userMatchesFilter);
    setHTML("admin-users", list.length ? list.map(userRow).join("") : `<p class="muted small">No users in this filter.</p>`);
    const plans = state.config?.plans || [];
    const sel = $("adduser-plan");
    if (!sel.options.length) sel.innerHTML = plans.map((p) => `<option value="${esc(p.code)}" ${p.code === "1m" ? "selected" : ""}>${esc(p.label)}</option>`).join("");
    if (isOwner()) {
      setHTML("admin-recent", (a.recent || []).length ? a.recent.map(histRow).join("") : `<p class="muted small">Nothing yet.</p>`);
      setHTML("admin-audit", (a.audit || []).length ? a.audit.map(auditRow).join("") : `<p class="muted small">Nothing yet.</p>`);
    }
  }
  let adminInflight = null;
  function refreshAdmin(loud) {
    if (!isAdmin()) return Promise.resolve();
    if (adminInflight) return adminInflight;                       // never stack overlapping requests
    adminInflight = (async () => {
      try { state.admin = await api("/api/admin/overview"); renderAdmin(); }
      catch (e) { if (loud || !e.network) toast(e.message, "err"); }   // background hiccups stay silent
      finally { adminInflight = null; }
    })();
    return adminInflight;
  }
  $("user-filters").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-f]"); if (!b) return;
    ev.stopPropagation();
    if (state.filter === b.dataset.f) return;
    state.filter = b.dataset.f;
    document.querySelectorAll("#user-filters .choice").forEach((x) => x.classList.toggle("on", x === b));
    renderAdmin(); animate($("admin-users"), "enter-l", 400);
  }, true);

  // ── Admin: actions ────────────────────────────────────────────────────
  async function userAction(action, uid, extra = {}, confirmMsg) {
    if (confirmMsg && !(await ask(confirmMsg))) return false;
    try {
      const r = await api("/api/admin/users", { method: "POST", body: { action, id: Number(uid), ...extra } });
      toast({ approve: "✅ Approved", extend: "⏱ Extended", reject: "❌ Rejected", revoke: "🔒 Revoked", ban: "🚫 Banned",
              unban: "♻️ Unbanned", promote: "🛡 Promoted", demote: "Demoted" }[action] || "Done", "ok");
      haptic("ok");
      await refreshAdmin();
      if (state.sheetUser && state.sheetUser.id === Number(uid)) openUserSheet(uid, r.user);
      return true;
    } catch (e) { toast(e.message, "err"); haptic("err"); return false; }
  }
  document.addEventListener("click", async (ev) => {
    const act = ev.target.closest("[data-act]");
    if (act) {
      ev.stopPropagation();
      const { act: action, uid, dur } = act.dataset;
      const extra = dur ? { duration: dur } : {};
      const need = action === "reject" ? "Reject this request?" : null;
      return userAction(action, uid, extra, need);
    }
    const open = ev.target.closest("[data-open-user]");
    if (open) { ev.stopPropagation(); return openUserSheet(open.dataset.openUser); }
    const row = ev.target.closest(".user[data-user]");
    if (row && !ev.target.closest("button")) openUserSheet(row.dataset.user);
  });
  $("adduser-form").onsubmit = async (e) => {
    e.preventDefault(); const id = Number($("adduser-id").value); if (!id) return;
    if (await userAction("approve", id, { duration: $("adduser-plan").value })) $("adduser-id").value = "";
  };
  $("bc-send").onclick = async () => {
    const text = $("bc-text").value.trim(); if (!text) return;
    if (!(await ask("Send to all approved users?"))) return;
    $("bc-send").disabled = true;
    try { const r = await api("/api/admin/broadcast", { method: "POST", body: { text } }); $("bc-text").value = ""; toast(`📣 Sending to ${r.recipients} users`, "ok"); haptic("ok"); }
    catch (e) { toast(e.message, "err"); haptic("err"); } finally { $("bc-send").disabled = false; }
  };

  // ── Admin: user sheet ─────────────────────────────────────────────────
  async function openUserSheet(uid, prefetched) {
    uid = Number(uid);
    let u = prefetched, hist = null;
    if (!u) {
      try { const r = await api("/api/admin/user/" + uid); u = r.user; hist = r.history; }
      catch (e) { u = (state.admin?.users || []).find((x) => x.id === uid); if (!u) return toast(e.message, "err"); }
    }
    state.sheetUser = u;
    const a = u.access || {}, owner = u.role === "owner", admin = u.role === "admin";
    $("usheet-title").innerHTML = `${owner ? "👑" : admin ? "🛡" : "👤"} ${esc(u.name)}${u.username ? ` <span class="muted tiny">@${esc(u.username)}</span>` : ""}`;
    const rows = [["ID", String(u.id)], ["Status", owner ? "👑 Owner" : `${a.icon || ""} ${a.label || ""}`]];
    if (!owner) {
      if (a.status === "approved") rows.push(["Validity", a.lifetime ? "♾ Lifetime" : `${a.expiry_label || ""} (${fmtDate(a.expires)})`]);
      if (a.plan_label) rows.push(["Plan", a.plan_label]);
      if (a.approved_at) rows.push(["Approved", `${fmtDate(a.approved_at)}${a.approved_by ? " · by " + a.approved_by : ""}`]);
      if (a.requested_at) rows.push(["Requested", `${fmtAgo(a.requested_at)} · ${a.requests || 0}×`]);
      if (a.note) rows.push(["Note", "“" + a.note + "”"]);
      if (a.reason) rows.push(["Reason", a.reason]);
    }
    rows.push(["Jobs", `${fmtInt(u.stats?.jobs || 0)} · ${fmtInt(u.stats?.parts || 0)} parts · ${fmtInt(u.stats?.chars || 0)} chars`]);
    rows.push(["Joined", `${fmtDate(u.joined)} · seen ${fmtAgo(u.last_seen)}`]);
    if (u.env) rows.push(["Source", "env (AUTHORIZED_USERS / ADMIN_USERS)"]);
    let html = rows.map(([k, v]) => `<div class="access-row"><span class="muted">${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
    const ah = (a.history || []);
    if (ah.length) html += `<div class="tiny muted" style="margin-top:8px">Access history</div>` +
      ah.map((h) => `<div class="hist-line">• ${fmtDate(h.ts)} · ${esc(h.action)}${h.expires ? " → " + fmtDate(h.expires) : /approve|extend/.test(h.action) && h.expires === 0 ? " → ♾" : ""}${h.reason ? " · “" + esc(h.reason) + "”" : ""}</div>`).join("");
    if (hist && hist.length) html += `<div class="tiny muted" style="margin-top:8px">Recent jobs</div>` +
      hist.slice(0, 5).map((h) => `<div class="hist-line">• ${esc(h.name)} · ${esc(h.status)} · ${fmtAgo(h.ts)}</div>`).join("");
    $("usheet-body").innerHTML = html;

    const canModerate = !owner && (isOwner() || !admin);
    const approved = a.status === "approved";
    const plans = state.config?.plans || [];
    $("usheet-plans").innerHTML = canModerate && a.status !== "banned"
      ? plans.map((p) => `<button class="choice" data-act="${approved ? "extend" : "approve"}" data-uid="${u.id}" data-dur="${esc(p.code)}">${approved ? "＋" : "✅"} ${esc(p.label)}</button>`).join("") : "";
    $("usheet-custom-row").classList.toggle("hidden", !canModerate || a.status === "banned");
    $("usheet-approve-custom").textContent = approved ? "＋ Extend" : "✅ Approve";
    $("usheet-reason").classList.toggle("hidden", !canModerate);
    $("usheet-reason").value = ""; $("usheet-duration").value = "";
    const acts = [];
    if (canModerate) {
      if (a.status === "pending") acts.push(`<button class="btn danger" data-act="reject" data-uid="${u.id}" data-reason="1">❌ Reject</button>`);
      if (approved) acts.push(`<button class="btn warn" data-act="revoke" data-uid="${u.id}" data-reason="1">🔒 Revoke</button>`);
      if (a.status === "banned") acts.push(`<button class="btn ok" data-act="unban" data-uid="${u.id}">♻️ Unban</button>`);
      else acts.push(`<button class="btn danger" data-act="ban" data-uid="${u.id}" data-reason="1">🚫 Ban</button>`);
      if (isOwner()) acts.push(admin ? `<button class="btn" data-act="demote" data-uid="${u.id}">Demote admin</button>`
                                     : `<button class="btn" data-act="promote" data-uid="${u.id}">🛡 Make admin</button>`);
    } else if (owner) {
      acts.push(`<p class="muted small">The owner cannot be modified.</p>`);
    } else {
      acts.push(`<p class="muted small">Only the owner can moderate an admin.</p>`);
    }
    $("usheet-actions").innerHTML = acts.join("");
    if ($("usheet").classList.contains("hidden")) openSheetEl("usheet");
    if (tg && tg.BackButton) { tg.BackButton.show(); tg.BackButton.onClick(closeUserSheet); }
  }
  function closeUserSheet() {
    closeSheetEl("usheet"); state.sheetUser = null;
    if (tg && tg.BackButton) { tg.BackButton.hide(); tg.BackButton.offClick(closeUserSheet); }
  }
  $("usheet-close").onclick = closeUserSheet;
  $("usheet").addEventListener("click", (e) => { if (e.target === $("usheet")) closeUserSheet(); });
  // Sheet buttons: attach reason / custom duration before the global handler runs
  $("usheet").addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-act]"); if (!b || !state.sheetUser) return;
    ev.stopPropagation(); ev.preventDefault();
    const action = b.dataset.act, uid = state.sheetUser.id;
    const extra = {};
    if (b.dataset.dur) extra.duration = b.dataset.dur;
    const reason = $("usheet-reason").value.trim(); if (reason) extra.reason = reason;
    const confirms = { ban: "Ban this user? All their jobs will be cancelled.", revoke: "Revoke access for this user?",
                       reject: "Reject this request?", demote: "Remove admin rights?" };
    await userAction(action, uid, extra, confirms[action]);
  }, true);
  $("usheet-approve-custom").onclick = async () => {
    if (!state.sheetUser) return;
    const spec = $("usheet-duration").value.trim(); if (!spec) return toast("Enter a duration, e.g. 45d or forever", "err");
    const extra = { duration: spec }; const reason = $("usheet-reason").value.trim(); if (reason) extra.reason = reason;
    await userAction(state.sheetUser.access?.status === "approved" ? "extend" : "approve", state.sheetUser.id, extra);
  };

  // ── Tabs ──────────────────────────────────────────────────────────────
  // Animated: the new tab slides in from the side it lives on in the bottom nav
  function setTab(name, opts = {}) {
    const prev = state.tab;
    if (prev === name && !opts.force) { window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    state.tab = name;
    const dir = TAB_ORDER.indexOf(name) >= TAB_ORDER.indexOf(prev) ? "enter-l" : "enter-r";
    document.querySelectorAll(".tab").forEach((t) => {
      const on = t.id === "tab-" + name;
      t.classList.remove("enter-l", "enter-r");
      t.classList.toggle("hidden", !on);
      if (on) animate(t, dir, 400);
    });
    document.querySelectorAll(".nav").forEach((n) => n.classList.toggle("active", n.dataset.tab === name));
    if (name === "admin") refreshAdmin();
    window.scrollTo({ top: 0 });
  }
  document.querySelectorAll(".nav").forEach((n) => n.onclick = () => setTab(n.dataset.tab));

  // ── Polling ───────────────────────────────────────────────────────────
  // One scheduler, one request in flight at a time. Fast while something is
  // running, slow when idle, paused while the app is in the background and
  // backing off (silently) when the server is unreachable. Renders are diffed
  // (see setHTML) so a poll never causes visible flicker.
  function schedulePoll(ms) { clearTimeout(state.timer); state.timer = setTimeout(() => refreshJobs(), ms); }
  let jobsInflight = null;
  function refreshJobs(immediate) {
    if (jobsInflight) return jobsInflight;
    if (document.hidden && !immediate) { clearTimeout(state.timer); return Promise.resolve(); }
    jobsInflight = (async () => {
      let next = POLL_IDLE, stop = false;
      try {
        state.jobs = await api("/api/jobs", { timeout: 15000 });
        renderJobs(); state.pollFails = 0; state.lastPoll = Date.now();
        if (state.jobs.active || (state.jobs.queue || []).length) next = POLL_LIVE;
        if (state.tab === "admin" && isAdmin()) refreshAdmin();
      } catch (e) {
        if (e.code === "locked") { stop = true; showLocked(e.data); }
        else if (e.code === "auth" || e.status === 401) { stop = true; sessionExpired(); }
        else {
          state.pollFails++;
          next = Math.min(POLL_MAX_BACKOFF, POLL_IDLE * Math.pow(1.6, state.pollFails - 1));
          if (state.pollFails === 3) toast("⚠️ Connection lost — retrying in background", "warn");
        }
      } finally { jobsInflight = null; }
      if (stop) clearTimeout(state.timer); else if (!document.hidden) schedulePoll(next);
    })();
    return jobsInflight;
  }
  async function refreshMe() {
    try { const r = await api("/api/me"); state.me = r.me; state.config = r.config; state.lastMe = Date.now(); renderMe(); renderSettings(); }
    catch (e) { if (e.code === "auth" || e.status === 401) sessionExpired(); }
  }
  function sessionExpired() {
    clearTimeout(state.timer); clearTimeout(state.lockedTimer);
    showAuth("Session expired", "Your Telegram session for this dashboard has expired.\nClose the Mini App and open it again from the bot.");
  }
  function onForeground() {
    if (state.me && !$("view-main").classList.contains("hidden")) {
      refreshJobs(true);                                                  // returns the in-flight promise if any
      if (Date.now() - state.lastMe > 30000) refreshMe();                 // profile rarely changes — don't hammer
    } else if (!$("view-locked").classList.contains("hidden")) recheckAccess();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(state.timer); return; }        // pause polling in the background
    onForeground();
  });
  // Telegram ≥ 8.0 keeps minimised Mini Apps alive and reports activation separately
  if (tg && tg.onEvent) { try { tg.onEvent("activated", onForeground); tg.onEvent("deactivated", () => clearTimeout(state.timer)); } catch (_) {} }

  // ── Manual refresh: ↻ button + pull-to-refresh ──
  async function refreshAll(source) {
    if (state.refreshing) return; state.refreshing = true;
    const btn = $("btn-refresh"), ptr = $("ptr");
    btn.classList.add("spin"); if (source === "pull") ptr.className = "ptr loading";
    const t0 = Date.now();
    try {
      await Promise.all([refreshMe(), refreshJobs(true), isAdmin() ? refreshAdmin(true) : null]);
      await sleep(Math.max(0, 500 - (Date.now() - t0)));   // let the spinner be seen
      if (state.pollFails) throw new Error("Server unreachable — will keep retrying");
      haptic("ok"); toast("✨ Updated", "ok");
    } catch (e) { toast(e.message || "Refresh failed", "err"); haptic("err"); }
    finally {
      btn.classList.remove("spin"); ptr.className = "ptr"; ptr.style.transform = "";
      $("content").style.transform = ""; state.refreshing = false;
    }
  }
  $("btn-refresh").onclick = () => refreshAll("button");

  (function pullToRefresh() {
    const THRESH = 40, MAX = 110;                            // px of (damped) travel to arm
    let startY = 0, dy = 0, active = false, armed = false;
    const content = $("content"), ptr = $("ptr");
    const canPull = () => !$("view-main").classList.contains("hidden") && window.scrollY <= 0 && !state.refreshing
      && $("sheet").classList.contains("hidden") && $("usheet").classList.contains("hidden");
    document.addEventListener("touchstart", (e) => {
      if (!canPull() || e.touches.length !== 1) return;
      startY = e.touches[0].clientY; dy = 0; active = true; armed = false;
    }, { passive: true });
    document.addEventListener("touchmove", (e) => {
      if (!active) return;
      dy = e.touches[0].clientY - startY;
      if (dy <= 0 || window.scrollY > 0) { if (dy < 0) active = false; content.style.transform = ""; ptr.className = "ptr"; return; }
      const d = Math.min(MAX, dy * 0.55);                     // rubber-band damping
      content.classList.add("pulling"); ptr.classList.add("pulling");
      content.style.transform = `translateY(${d}px)`;
      ptr.style.opacity = Math.min(1, d / 40);
      ptr.style.transform = `translate(-50%, ${d - 46}px) rotate(${d * 3}deg)`;
      const nowArmed = d >= THRESH;
      if (nowArmed !== armed) { armed = nowArmed; ptr.classList.toggle("armed", armed); haptic(armed ? "medium" : "light"); }
    }, { passive: true });
    const end = () => {
      if (!active) return; active = false;
      content.classList.remove("pulling"); ptr.classList.remove("pulling");
      ptr.style.opacity = "";
      if (armed) { content.style.transform = "translateY(46px)"; refreshAll("pull"); }
      else { content.style.transform = ""; ptr.style.transform = ""; ptr.className = "ptr"; }
      armed = false;
    };
    document.addEventListener("touchend", end, { passive: true });
    document.addEventListener("touchcancel", end, { passive: true });
  })();

  // ── Locked view (approval flow) ───────────────────────────────────────
  function showLocked(data) {
    const me = (data && data.me) || state.me || {}, a = me.access || {}, cfg = (data && data.config) || state.config || {};
    state.me = me; state.config = cfg;
    const status = a.status || (data && data.status) || "none";
    $("locked-id").textContent = me.id || (data && data.id) || "—";
    const icon = { none: "🔒", pending: "⏳", approved: "✅", expired: "⌛", rejected: "❌", banned: "🚫" }[status] || "🔒";
    const title = { none: "Private Bot", pending: "Request Pending", expired: "Access Expired", rejected: "Request Declined", banned: "Access Blocked" }[status] || "Private Bot";
    const text = {
      none: "Access is granted by the owner. Send a request and you will be notified in the chat.",
      pending: "Your request is waiting for the owner. You will get a message in the chat as soon as it is decided.",
      expired: "Your access period has ended. Send a new request to continue.",
      rejected: a.cooldown ? `Your last request was declined. You may try again in ${fmtDur(a.cooldown)}.` : "Your last request was declined. You may send a new request.",
      banned: "You have been blocked from using this bot.",
    }[status] || "";
    $("locked-icon").textContent = icon; $("locked-title").textContent = title; $("locked-text").textContent = text;
    $("locked-status").innerHTML = stChip({ status, icon: a.icon || icon, label: a.label || title });
    $("locked-card").classList.remove("hidden");
    const setRow = (id, val, show) => { $(id).classList.toggle("hidden", !show); if (show) $(id).querySelector("b").textContent = val; };
    setRow("locked-row-req", a.requested_at ? `${fmtAgo(a.requested_at)} · ${a.requests || 1}×` : "", status === "pending");
    setRow("locked-row-reason", a.reason || "", !!a.reason && (status === "rejected" || status === "banned" || status === "none"));
    setRow("locked-row-exp", a.expires ? fmtDate(a.expires) : "", status === "expired" && !!a.expires);
    $("request-form").classList.toggle("hidden", !a.can_request);
    $("pending-actions").classList.toggle("hidden", status !== "pending");
    $("btn-withdraw").classList.toggle("hidden", status !== "pending");
    $("locked-error").classList.add("hidden");
    show("locked");
    // Auto re-check while pending so approval is picked up without a manual tap
    clearTimeout(state.lockedTimer);
    if (status === "pending" || (status === "rejected" && a.cooldown)) state.lockedTimer = setTimeout(recheckAccess, 15000);
  }
  async function recheckAccess(manual) {
    try {
      const r = await api("/api/me");            // succeeds only when approved
      haptic("ok"); toast("✅ Access granted", "ok"); boot(r);
    } catch (e) {
      if (e.code === "locked") { showLocked(e.data); if (manual) { toast("Still waiting…"); haptic("warn"); } }
      else if (e.code === "auth" || e.status === 401) showAuth();
      else if (e.network && !manual) { clearTimeout(state.lockedTimer); state.lockedTimer = setTimeout(recheckAccess, 20000); }  // quiet retry
      else { $("locked-error").textContent = e.message; $("locked-error").classList.remove("hidden"); haptic("err"); }
    }
  }
  $("request-form").onsubmit = async (e) => {
    e.preventDefault(); $("locked-error").classList.add("hidden");
    const btn = e.target.querySelector("button"); btn.disabled = true; haptic("medium");
    try {
      const r = await api("/api/access/request", { method: "POST", body: { note: $("request-note").value.trim() } });
      $("request-note").value = "";
      if (r.already) return boot(r);
      haptic("ok"); toast(r.sent ? "📨 Request sent" : r.why === "noted" ? "📝 Note added to your request" : "Request already pending", "ok");
      showLocked({ me: r.me, config: r.config || state.config });
    } catch (er) {
      if (er.data && er.data.me) showLocked({ me: er.data.me, config: state.config });
      $("locked-error").textContent = er.message; $("locked-error").classList.remove("hidden"); haptic("err");
    } finally { btn.disabled = false; }
  };
  $("btn-recheck").onclick = async () => { $("btn-recheck").disabled = true; await recheckAccess(true); $("btn-recheck").disabled = false; };
  $("btn-withdraw").onclick = async () => {
    if (!(await ask("Withdraw your access request?"))) return;
    try { const r = await api("/api/access/withdraw", { method: "POST", body: {} }); toast("Request withdrawn"); haptic("ok"); showLocked({ me: r.me, config: state.config }); }
    catch (e) { toast(e.message, "err"); haptic("err"); }
  };

  // ── Boot ──────────────────────────────────────────────────────────────
  function boot(r) {
    clearTimeout(state.lockedTimer);
    state.me = r.me; state.config = r.config; state.lastMe = Date.now();
    renderMe(); renderSettings(); show("main");
    const firstBoot = !state.booted; state.booted = true;
    refreshJobs(true).then(() => {
      if (!firstBoot) return;
      const m = location.hash.match(/job=([A-Za-z0-9_-]+)/);
      if (m) openSheet(m[1]);
      if (/admin/.test(location.hash) && isAdmin()) setTab("admin");
    });
    if (isAdmin()) refreshAdmin();
    haptic("soft");
  }
  async function showAuth(title, text) {
    setText("auth-title", title || "Open from Telegram");
    if (text) setText("auth-text", text);
    show("auth");
    try { const h = await fetch("/health", { cache: "no-store" }).then((x) => x.json()); if (h.bot) { $("auth-link").href = "https://t.me/" + h.bot; $("auth-link").classList.remove("hidden"); } } catch (_) {}
  }
  function setLoading(msg, failed) {
    const v = $("view-loading"); v.classList.toggle("failed", !!failed);
    setText("loading-status", msg); $("btn-retry").classList.toggle("hidden", !failed);
  }
  // Free-tier hosts (Render) put the server to sleep; the first request after a
  // while can take 30-60 s or fail with 502/503 while it boots. Keep the skeleton
  // up, tell the user what is going on and retry with a gentle backoff instead
  // of dumping them on a misleading error screen.
  const BOOT_DELAYS = [1200, 2000, 3000, 5000, 8000, 8000, 10000, 10000, 15000, 15000];
  let starting = false;
  async function start() {
    if (starting) return; starting = true;
    show("loading");
    try {
      for (let attempt = 0; ; attempt++) {
        setLoading(attempt === 0 ? "Connecting…" : attempt < 3 ? "Waking up the server…" : `Still waking up the server… (${attempt + 1})`);
        try { return boot(await api("/api/me", { timeout: attempt === 0 ? 12000 : 25000 })); }
        catch (e) {
          if (e.code === "locked") return showLocked(e.data);
          if (e.code === "auth" || e.status === 401) return showAuth();
          if (!e.network && e.status && e.status < 500 && e.status !== 408 && e.status !== 429)
            return setLoading("⚠️ " + e.message, true);
          if (attempt >= BOOT_DELAYS.length)
            return setLoading("⚠️ Could not reach the server. Check your connection and tap Retry.", true);
          await sleep(BOOT_DELAYS[attempt]);
        }
      }
    } finally { starting = false; }
  }
  $("btn-retry").onclick = () => { haptic("medium"); start(); };
  window.addEventListener("online", () => { if (!state.booted && !starting) start(); else if (state.booted) onForeground(); });
  start();
})();
