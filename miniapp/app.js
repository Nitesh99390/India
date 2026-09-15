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
                  filter: "all", sheetUser: null, lockedTimer: null };

  // ── Telegram bootstrap ────────────────────────────────────────────────
  if (tg) {
    try { tg.ready(); tg.expand(); } catch (_) {}
    try { tg.setHeaderColor && tg.setHeaderColor("secondary_bg_color"); } catch (_) {}
    try { tg.disableClosingConfirmation && tg.disableClosingConfirmation(); } catch (_) {}
  }
  const haptic = (t) => { try { tg && tg.HapticFeedback && (t === "err" ? tg.HapticFeedback.notificationOccurred("error")
    : t === "ok" ? tg.HapticFeedback.notificationOccurred("success") : tg.HapticFeedback.impactOccurred("light")); } catch (_) {} };
  const ask = (msg) => tg && tg.showConfirm ? new Promise((r) => tg.showConfirm(msg, r)) : Promise.resolve(confirm(msg));

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
    $("me-name").textContent = me.name;
    const role = $("me-role"); role.textContent = me.owner ? "owner" : me.admin ? "admin" : me.role;
    role.className = "badge " + (me.owner ? "owner" : me.admin ? "admin" : "");
    $("me-sub").textContent = (me.username ? "@" + me.username + " · " : "") + "ID " + me.id;
    if (me.photo) { $("avatar").src = me.photo; $("avatar").classList.remove("hidden"); $("avatar-fallback").classList.add("hidden"); }
    const pill = $("db-pill"); pill.textContent = cfg.db ? "🗄 MongoDB" : "🧠 RAM"; pill.classList.toggle("on", !!cfg.db);
    pill.title = cfg.db ? "Persistent storage: " + cfg.db_name : "In-memory — settings reset on restart";
    $("s-jobs").textContent = fmtInt(me.stats.jobs); $("s-parts").textContent = fmtInt(me.stats.parts); $("s-chars").textContent = fmtInt(me.stats.chars);
    $("nav-admin").classList.toggle("hidden", !isAdmin());
    document.querySelectorAll(".owner-only").forEach((el) => el.classList.toggle("hidden", !me.owner));
    $("pdf-ext").textContent = (cfg.input_exts || []).includes(".pdf") ? " / .pdf" : "";
    $("settings-note").textContent = cfg.db ? "Saved to your profile — used by ⚡ Quick Start." : "⚠️ No database — settings reset when the server restarts.";
    $("split-hint").textContent = `0 = no split · ${cfg.split_range[0]} KB – ${Math.round(cfg.split_range[1] / 1024)} MB`;
    renderAccess();
  }

  // ── Render: my access card (home tab) ─────────────────────────────────
  function renderAccess() {
    const me = state.me, a = me.access || {}, cfg = state.config || {};
    const chip = $("access-chip");
    chip.innerHTML = me.owner ? "👑 Owner" : `${a.icon || ""} ${esc(a.label || "")}`;
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
    $("access-body").innerHTML = rows.map(([k, v]) =>
      `<div class="access-row"><span class="muted">${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
    // Banner: expiring soon
    const banner = $("access-banner");
    if (!me.owner && a.expires && a.days_left != null && a.days_left <= (cfg.reminder_days || 3)) {
      banner.className = "banner warn";
      banner.textContent = a.days_left <= 0 ? "⌛ Your access expires today. Ask the owner to extend it."
        : `⏳ Your access expires in ${a.days_left} day${a.days_left === 1 ? "" : "s"} (${fmtDate(a.expires)}).`;
    } else { banner.className = "banner warn hidden"; }
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
      ${(j.mine || isOwner()) && (running || j.status === "queued") && !opts.noActions
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
      <div class="job-meta">${langLabel(h.lang)} · ${fmtLabel(h.fmt)} · ${fmtSize(h.size)}${h.parts ? " · " + h.parts + " parts" : ""}${h.chars ? " · " + fmtInt(h.chars) + " chars" : ""}${h.secs ? " · " + fmtTime(h.secs) : ""}${h.error ? " · " + esc(h.error) : ""}${h.user && isOwner() ? " · 👤 " + esc(h.user) : ""} · ${fmtAgo(h.ts)}</div></div>`;
  }
  document.addEventListener("click", async (ev) => {
    const c = ev.target.closest("[data-cancel]");
    if (c) {
      haptic();
      if (!(await ask("Cancel this job?"))) return;
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
    return `<div class="audit"><b>${who}</b> ${esc(e.action)}${tgt ? " → <b>" + tgt + "</b>" : ""}${extra ? " · " + extra : ""} <span class="tiny">· ${fmtAgo(e.ts)}</span></div>`;
  }
  function renderAdmin() {
    const a = state.admin; if (!a) return;
    $("a-users").textContent = fmtInt(a.users.length); $("a-jobs").textContent = fmtInt(a.stats.jobs);
    $("a-uptime").textContent = fmtUptime(a.uptime); $("a-parts").textContent = fmtInt(a.stats.parts);
    $("a-failed").textContent = fmtInt(a.stats.failed); $("a-cancel").textContent = fmtInt(a.stats.cancelled);
    const bud = a.db_budget; $("db-card").classList.toggle("hidden", !bud);
    if (bud) {
      const pct = Math.min(100, bud.percent || 0);
      $("db-bar").style.width = pct + "%"; $("db-bar").style.background = pct > 85 ? "var(--danger, #ef4444)" : "";
      $("db-size").textContent = `${(bud.size_mb || 0).toFixed(1)} / ${bud.budget_mb} MB (${pct}%)`;
      $("db-docs").textContent = `${fmtInt(bud.job_docs)} / ${fmtInt(bud.max_job_docs)} job docs · TTL ${bud.ttl_days} d${bud.pruned ? " · pruned " + fmtInt(bud.pruned) : ""}`;
    }
    const pending = a.pending || [];
    $("a-pending").textContent = pending.length;
    $("admin-pending").innerHTML = pending.length ? pending.map(reqRow).join("") : `<p class="muted small">No pending requests. 🎉</p>`;
    const c = a.counts || {};
    $("a-counts").textContent = `✅ ${c.approved || 0} · ⏳ ${c.pending || 0} · ⌛ ${c.expired || 0}`;
    const list = a.users.filter(userMatchesFilter);
    $("admin-users").innerHTML = list.length ? list.map(userRow).join("") : `<p class="muted small">No users in this filter.</p>`;
    const plans = state.config?.plans || [];
    const sel = $("adduser-plan");
    if (!sel.options.length) sel.innerHTML = plans.map((p) => `<option value="${esc(p.code)}" ${p.code === "1m" ? "selected" : ""}>${esc(p.label)}</option>`).join("");
    if (isOwner()) {
      $("admin-recent").innerHTML = (a.recent || []).length ? a.recent.map(histRow).join("") : `<p class="muted small">Nothing yet.</p>`;
      $("admin-audit").innerHTML = (a.audit || []).length ? a.audit.map(auditRow).join("") : `<p class="muted small">Nothing yet.</p>`;
    }
  }
  async function refreshAdmin() {
    if (!isAdmin()) return;
    try { state.admin = await api("/api/admin/overview"); renderAdmin(); }
    catch (e) { toast(e.message, "err"); }
  }
  $("user-filters").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-f]"); if (!b) return;
    ev.stopPropagation();
    state.filter = b.dataset.f;
    document.querySelectorAll("#user-filters .choice").forEach((x) => x.classList.toggle("on", x === b));
    renderAdmin();
  }, true);

  // ── Admin: actions ────────────────────────────────────────────────────
  async function userAction(action, uid, extra = {}, confirmMsg) {
    if (confirmMsg && !(await ask(confirmMsg))) return false;
    haptic();
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
    try { const r = await api("/api/admin/broadcast", { method: "POST", body: { text } }); $("bc-text").value = ""; toast(`📣 Sending to ${r.recipients} users`, "ok"); }
    catch (e) { toast(e.message, "err"); } finally { $("bc-send").disabled = false; }
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
    $("usheet").classList.remove("hidden");
    if (tg && tg.BackButton) { tg.BackButton.show(); tg.BackButton.onClick(closeUserSheet); }
  }
  function closeUserSheet() {
    $("usheet").classList.add("hidden"); state.sheetUser = null;
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
    catch (e) { if (e.code === "locked") { clearTimeout(state.timer); return showLocked(e.data); } }
    const hasLive = state.jobs && (state.jobs.active || state.jobs.queue.length);
    clearTimeout(state.timer);
    state.timer = setTimeout(refreshJobs, hasLive ? 2500 : 8000);
    if (state.tab === "admin" && isAdmin()) refreshAdmin();
  }
  async function refreshMe() {
    try { const r = await api("/api/me"); state.me = r.me; state.config = r.config; renderMe(); renderSettings(); } catch (_) {}
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (state.me && !$("view-main").classList.contains("hidden")) { refreshJobs(); refreshMe(); }
    else if (!$("view-locked").classList.contains("hidden")) recheckAccess();
  });

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
  async function recheckAccess() {
    try {
      const r = await api("/api/me");            // succeeds only when approved
      haptic("ok"); toast("✅ Access granted", "ok"); boot(r);
    } catch (e) {
      if (e.code === "locked") showLocked(e.data);
      else if (e.code === "auth" || e.status === 401) show("auth");
      else { $("locked-error").textContent = e.message; $("locked-error").classList.remove("hidden"); }
    }
  }
  $("request-form").onsubmit = async (e) => {
    e.preventDefault(); $("locked-error").classList.add("hidden");
    const btn = e.target.querySelector("button"); btn.disabled = true; haptic();
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
  $("btn-recheck").onclick = async () => { haptic(); $("btn-recheck").disabled = true; await recheckAccess(); $("btn-recheck").disabled = false; };
  $("btn-withdraw").onclick = async () => {
    if (!(await ask("Withdraw your access request?"))) return;
    try { const r = await api("/api/access/withdraw", { method: "POST", body: {} }); toast("Request withdrawn"); showLocked({ me: r.me, config: state.config }); }
    catch (e) { toast(e.message, "err"); }
  };

  // ── Boot ──────────────────────────────────────────────────────────────
  function boot(r) {
    clearTimeout(state.lockedTimer);
    state.me = r.me; state.config = r.config;
    renderMe(); renderSettings(); show("main");
    refreshJobs().then(() => {
      const m = location.hash.match(/job=([A-Za-z0-9_-]+)/);
      if (m) openSheet(m[1]);
      if (/admin/.test(location.hash) && isAdmin()) setTab("admin");
    });
    if (isAdmin()) refreshAdmin();
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
