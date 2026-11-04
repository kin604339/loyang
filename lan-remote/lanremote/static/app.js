/* LAN Remote viewer */
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const state = {
    ws: null, info: null, frameW: 0, frameH: 0, bytes: 0, frames: 0, latency: 0, viewOnly: false,
    paused: false, unread: 0, panel: null, fsPath: "", peersTimer: null,
    opts: JSON.parse(localStorage.getItem("lr_opts") || "{}"),
  };
  const canvas = $("#screen"), ctx = canvas.getContext("2d"), stage = $("#stage"), cursorEl = $("#cursor");

  // ---------------------------------------------------------------- helpers
  const api = async (path, opts = {}) => {
    const r = await fetch(path, { credentials: "same-origin", ...opts });
    if (r.status === 401) { showLogin(); throw new Error("unauthorized"); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  };
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const toast = (text) => { const d = document.createElement("div"); d.textContent = text; $("#toasts").appendChild(d); setTimeout(() => d.remove(), 4200); };
  const send = (obj) => { if (state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify(obj)); };
  const fmtSize = (n) => n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : n < 1073741824 ? (n / 1048576).toFixed(1) + " MB" : (n / 1073741824).toFixed(2) + " GB";
  const saveOpts = () => localStorage.setItem("lr_opts", JSON.stringify(state.opts));
  const inInput = () => { const a = document.activeElement; return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT"); };

  // ---------------------------------------------------------------- login
  async function loadInfo() {
    try {
      const info = await api("/api/info");
      $("#loginHost").textContent = `${info.name} (${info.os})`;
      $("#loginDemo").classList.toggle("hidden", !info.demo);
    } catch (e) { $("#loginHost").textContent = "?"; }
  }
  function renderPeers(ul, peers, withWol) {
    ul.innerHTML = "";
    if (!peers.length) { ul.innerHTML = '<li class="muted">目前沒有找到其他電腦（對方需要執行 LAN Remote，且在同一個網路）</li>'; return; }
    for (const p of peers) {
      const li = document.createElement("li");
      const url = `http://${p.ip}:${p.port || 7788}/`;
      li.innerHTML = `<div><div class="n"></div><div class="d"></div></div><div class="row"></div>`;
      li.querySelector(".n").textContent = p.name;
      li.querySelector(".d").textContent = `${p.ip} · ${p.os || ""}${p.mac ? " · " + p.mac : ""}`;
      const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.innerHTML = '<button class="primary">連線</button>';
      li.querySelector(".row").appendChild(a);
      if (withWol && p.mac) { const b = document.createElement("button"); b.textContent = "喚醒"; b.onclick = () => wol(p.mac); li.querySelector(".row").appendChild(b); }
      ul.appendChild(li);
    }
  }
  async function pollPeers() {
    try {
      const d = await api("/api/peers");
      if (!$("#login").classList.contains("hidden")) renderPeers($("#loginPeers"), d.peers, false);
      if (state.panel === "devices") renderPeers($("#peerList"), d.peers, true);
    } catch (e) { /* ignore */ }
  }
  function showLogin() {
    if (state.ws) { try { state.ws.close(); } catch (e) {} state.ws = null; }
    $("#app").classList.add("hidden"); $("#login").classList.remove("hidden");
    $("#myName").value = state.opts.name || "";
    $("#pin").focus();
  }
  async function login() {
    const name = $("#myName").value.trim(), pin = $("#pin").value.trim();
    if (!pin) { $("#loginError").textContent = "請輸入 PIN 碼"; return; }
    $("#btnLogin").disabled = true; $("#btnLogin").textContent = "連線中…（對方可能需要按允許）"; $("#loginError").textContent = "";
    try {
      await post("/api/login", { name, pin });
      state.opts.name = name; saveOpts();
      enterApp();
    } catch (e) { $("#loginError").textContent = e.message; }
    $("#btnLogin").disabled = false; $("#btnLogin").textContent = "連線";
  }
  $("#btnLogin").onclick = login;
  const manualConnect = () => {
    let t = $("#manualAddr").value.trim(); if (!t) return;
    if (!t.includes("://")) t = "http://" + t;
    if ((t.match(/:/g) || []).length < 2) t = t.replace(/\/$/, "") + ":" + (location.port || 7788);
    window.open(t.replace(/\/$/, "") + "/", "_blank");
  };
  $("#btnManual").onclick = manualConnect;
  $("#manualAddr").addEventListener("keydown", (e) => { if (e.key === "Enter") manualConnect(); });
  $("#pin").addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });

  // ---------------------------------------------------------------- app / websocket
  function enterApp() {
    $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
    applyOpts();
    connectWs();
    stage.focus();
  }
  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    state.ws = ws;
    $("#overlayMsg").textContent = "連線中…"; $("#overlayMsg").classList.remove("hidden");
    ws.onopen = () => { sendSettings(); };
    ws.onmessage = (ev) => { if (typeof ev.data === "string") onJson(JSON.parse(ev.data)); else onFrame(ev.data); };
    ws.onclose = () => {
      if (state.ws !== ws) return;
      $("#overlayMsg").textContent = "連線中斷，3 秒後重新連線…"; $("#overlayMsg").classList.remove("hidden");
      setTimeout(async () => {
        if (state.ws !== ws) return;
        try { await api("/api/session"); connectWs(); } catch (e) { /* showLogin called by api() */ }
      }, 3000);
    };
  }
  function sendSettings() {
    send({ t: "settings", monitor: +$("#selMonitor").value || 1, quality: +$("#selQuality").value, fps: +$("#selFps").value, scale: +$("#selScale").value, paused: state.paused });
  }
  function onJson(m) {
    switch (m.t) {
      case "info":
        state.info = m;
        $("#hostName").textContent = `${m.name} · ${m.os}${m.demo ? " · 示範模式" : ""}`;
        const sel = $("#selMonitor"); sel.innerHTML = "";
        for (const mon of m.monitors) { const o = document.createElement("option"); o.value = mon.index; o.textContent = mon.name; sel.appendChild(o); }
        if (m.monitors.length <= 1) sel.classList.add("hidden");
        if (state.opts.monitor && m.monitors.some((x) => x.index == state.opts.monitor)) sel.value = state.opts.monitor;
        if (m.view_only) toast("對方開啟了唯讀模式，只能觀看");
        $("#shellState").textContent = m.allow_shell ? "" : "（對方未開放）";
        sendSettings();
        renderConn();
        break;
      case "pong": state.latency = Date.now() - m.ts; break;
      case "cur": placeCursor(m.x, m.y); break;
      case "chat": addChat(m); break;
      case "clip":
        if (m.text) { navigator.clipboard?.writeText(m.text).then(() => toast("已複製對方的剪貼簿到本機")).catch(() => prompt("對方剪貼簿內容（請手動複製）", m.text)); }
        else toast("對方剪貼簿是空的（或沒有文字）");
        break;
      case "toast": toast(m.text); break;
    }
  }
  let drawChain = Promise.resolve();
  function onFrame(buf) {
    state.bytes += buf.byteLength; state.frames++;
    const view = new DataView(buf); const hl = view.getUint32(0);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
    const blob = new Blob([new Uint8Array(buf, 4 + hl)], { type: "image/jpeg" });
    const p = createImageBitmap(blob);
    drawChain = drawChain.then(async () => {
      const bmp = await p;
      if (header.full && (canvas.width !== header.fw || canvas.height !== header.fh)) { canvas.width = header.fw; canvas.height = header.fh; }
      state.frameW = header.fw; state.frameH = header.fh;
      ctx.drawImage(bmp, header.x, header.y);
      bmp.close && bmp.close();
      if (header.cx !== undefined) placeCursor(header.cx, header.cy);
      $("#overlayMsg").classList.add("hidden");
    }).catch(() => {});
  }
  function placeCursor(nx, ny) {
    if (!state.opts.showCursor) { cursorEl.style.display = "none"; return; }
    cursorEl.style.display = "";
    const r = canvas.getBoundingClientRect(), w = canvas.getBoundingClientRect();
    cursorEl.style.transform = `translate(${nx * w.width}px, ${ny * w.height}px)`;
  }
  // stats
  setInterval(() => {
    send({ t: "ping", ts: Date.now() });
    const kb = state.bytes / 1024; state.bytes = 0; const f = state.frames; state.frames = 0;
    $("#stats").textContent = `${f} fps · ${kb.toFixed(0)} KB/s · ${state.latency} ms · ${state.frameW}×${state.frameH}`;
  }, 1000);

  // ---------------------------------------------------------------- input
  const norm = (e) => { const r = canvas.getBoundingClientRect(); return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }; };
  const canControl = () => !state.viewOnly && !(state.info && state.info.view_only);
  let pendingMove = null, moveTimer = null;
  const flushMove = () => { if (pendingMove) { send({ t: "mm", ...pendingMove }); pendingMove = null; } moveTimer = null; };
  const BTN = ["left", "middle", "right", "back", "forward"];
  canvas.addEventListener("mousemove", (e) => { if (!canControl()) return; pendingMove = norm(e); if (!moveTimer) moveTimer = setTimeout(flushMove, 16); });
  canvas.addEventListener("mousedown", (e) => { e.preventDefault(); stage.focus(); if (!canControl()) return; flushMove(); send({ t: "md", b: BTN[e.button] || "left", ...norm(e) }); });
  canvas.addEventListener("mouseup", (e) => { e.preventDefault(); if (!canControl()) return; flushMove(); send({ t: "mu", b: BTN[e.button] || "left", ...norm(e) }); });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("dblclick", (e) => e.preventDefault());
  let wheelAcc = { x: 0, y: 0 };
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault(); if (!canControl()) return;
    const k = e.deltaMode === 1 ? 20 : e.deltaMode === 2 ? 200 : 1;
    wheelAcc.x += e.deltaX * k; wheelAcc.y += e.deltaY * k;
    const step = 60, dx = Math.trunc(wheelAcc.x / step), dy = Math.trunc(wheelAcc.y / step);
    if (dx || dy) { wheelAcc.x -= dx * step; wheelAcc.y -= dy * step; const inv = state.opts.invertScroll ? -1 : 1; send({ t: "wh", dx: dx * inv, dy: -dy * inv }); }
  }, { passive: false });
  // keyboard
  const keyActive = () => !inInput() && !$("#app").classList.contains("hidden") && canControl();
  document.addEventListener("keydown", (e) => {
    if (!keyActive()) return;
    if (e.key === "F11" && !document.fullscreenElement) return; // let browser go fullscreen
    e.preventDefault();
    send({ t: "kd", key: e.key, code: e.code });
  });
  document.addEventListener("keyup", (e) => { if (!keyActive()) return; e.preventDefault(); send({ t: "ku", key: e.key, code: e.code }); });
  window.addEventListener("blur", () => send({ t: "release_all" }));
  document.addEventListener("visibilitychange", () => { if (document.hidden) send({ t: "release_all" }); });
  // touch (phone / tablet)
  let touch = { start: 0, moved: false, last: null, longTimer: null, two: null };
  canvas.addEventListener("touchstart", (e) => {
    e.preventDefault(); if (!canControl()) return;
    if (e.touches.length === 2) { touch.two = { y: (e.touches[0].clientY + e.touches[1].clientY) / 2 }; clearTimeout(touch.longTimer); return; }
    const t = e.touches[0]; touch.start = Date.now(); touch.moved = false; touch.last = norm(t);
    send({ t: "mm", ...touch.last });
    touch.longTimer = setTimeout(() => { send({ t: "md", b: "right", ...touch.last }); send({ t: "mu", b: "right", ...touch.last }); touch.moved = true; }, 600);
  }, { passive: false });
  canvas.addEventListener("touchmove", (e) => {
    e.preventDefault(); if (!canControl()) return;
    if (e.touches.length === 2 && touch.two) {
      const y = (e.touches[0].clientY + e.touches[1].clientY) / 2, d = y - touch.two.y;
      if (Math.abs(d) > 30) { send({ t: "wh", dx: 0, dy: d > 0 ? 1 : -1 }); touch.two.y = y; }
      return;
    }
    const p = norm(e.touches[0]);
    if (Math.abs(p.x - touch.last.x) > 0.005 || Math.abs(p.y - touch.last.y) > 0.005) { touch.moved = true; clearTimeout(touch.longTimer); }
    touch.last = p; send({ t: "mm", ...p });
  }, { passive: false });
  canvas.addEventListener("touchend", (e) => {
    e.preventDefault(); clearTimeout(touch.longTimer); if (!canControl()) return;
    if (touch.two) { if (e.touches.length === 0) touch.two = null; return; }
    if (!touch.moved && Date.now() - touch.start < 500) { send({ t: "md", b: "left", ...touch.last }); send({ t: "mu", b: "left", ...touch.last }); }
  }, { passive: false });

  // ---------------------------------------------------------------- toolbar
  for (const id of ["selMonitor", "selQuality", "selFps", "selScale"]) {
    $("#" + id).addEventListener("change", () => { state.opts[id] = $("#" + id).value; if (id === "selMonitor") state.opts.monitor = $("#selMonitor").value; saveOpts(); sendSettings(); });
  }
  $$(".dd > button").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); const m = b.nextElementSibling; const open = m.classList.contains("open"); $$(".menu").forEach((x) => x.classList.remove("open")); m.classList.toggle("open", !open); }));
  document.addEventListener("click", () => $$(".menu").forEach((x) => x.classList.remove("open")));
  $$("#keysMenu button").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.action === "release") send({ t: "release_all" }); else send({ t: "combo", keys: b.dataset.combo.split(",") });
    stage.focus();
  }));
  $$("#clipMenu button").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.clip === "pull") send({ t: "clip_get" });
    else {
      let text = "";
      try { text = await navigator.clipboard.readText(); } catch (e) { text = prompt("瀏覽器不允許讀取剪貼簿，請貼上要送過去的文字：") || ""; }
      if (text) send({ t: "clip_set", text });
    }
  }));
  $$("[data-panel]").forEach((b) => b.addEventListener("click", () => openPanel(b.dataset.panel)));
  $("#btnClosePanel").onclick = () => openPanel(null);
  $("#btnLogout").onclick = async () => { try { await post("/api/logout"); } catch (e) {} showLogin(); };
  $("#btnFull").onclick = async () => {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    await document.documentElement.requestFullscreen().catch(() => {});
    try { await navigator.keyboard?.lock?.(); } catch (e) {}
  };
  document.addEventListener("fullscreenchange", () => { $("#app").classList.toggle("fullscreen", !!document.fullscreenElement); if (!document.fullscreenElement) navigator.keyboard?.unlock?.(); });

  const PANEL_TITLES = { text: "輸入文字", files: "檔案傳輸", chat: "聊天", system: "系統", devices: "區網裝置", settings: "設定" };
  function openPanel(name) {
    if (name === state.panel) name = null;
    state.panel = name;
    $("#panel").classList.toggle("hidden", !name);
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    if (name) $("#panelTitle").textContent = PANEL_TITLES[name];
    if (name === "files") fsLoad(state.fsPath);
    if (name === "chat") { state.unread = 0; $("#chatBadge").classList.add("hidden"); $("#chatInput").focus(); }
    if (name === "system") { loadSys(); loadAudit(); }
    if (name === "devices") pollPeers();
    if (name === "text") $("#typeText").focus();
    if (name === "settings") renderConn();
    if (!name) stage.focus();
  }

  // ---------------------------------------------------------------- text input
  const doType = (enter) => { const text = $("#typeText").value; if (!text && !enter) return; send({ t: "type", text, enter }); $("#typeText").value = ""; toast("已送出"); };
  $("#btnType").onclick = () => doType(false);
  $("#btnTypeEnter").onclick = () => doType(true);
  $("#typeText").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) doType(true); });

  // ---------------------------------------------------------------- files
  async function fsLoad(path) {
    try {
      const d = await api("/api/fs/list?path=" + encodeURIComponent(path || ""));
      state.fsPath = d.path; $("#fsPath").value = d.path; $("#fsUp").disabled = !d.parent; $("#fsUp").dataset.parent = d.parent || "";
      const sc = $("#fsShortcuts"); sc.innerHTML = "";
      for (const s of [...d.shortcuts, ...d.roots.map((r) => ({ name: r, path: r }))]) { const b = document.createElement("button"); b.textContent = s.name; b.onclick = () => fsLoad(s.path); sc.appendChild(b); }
      const ul = $("#fsList"); ul.innerHTML = "";
      if (!d.entries.length) ul.innerHTML = '<li class="muted">（空資料夾）</li>';
      for (const e of d.entries) {
        const full = d.path.endsWith(d.sep) ? d.path + e.name : d.path + d.sep + e.name;
        const li = document.createElement("li"); li.className = e.is_dir ? "dir" : "file";
        li.innerHTML = `<span class="ic">${e.is_dir ? "📁" : "📄"}</span><span class="nm"></span><span class="sz"></span><span class="ops"></span>`;
        li.querySelector(".nm").textContent = e.name; li.querySelector(".nm").title = new Date(e.mtime * 1000).toLocaleString();
        li.querySelector(".sz").textContent = e.is_dir ? "" : fmtSize(e.size);
        if (e.is_dir) li.querySelector(".nm").onclick = () => fsLoad(full);
        const ops = li.querySelector(".ops");
        const mk = (label, title, fn) => { const b = document.createElement("button"); b.textContent = label; b.title = title; b.onclick = fn; ops.appendChild(b); };
        mk("⬇", e.is_dir ? "下載（壓縮成 zip）" : "下載", () => { window.open("/api/fs/download?path=" + encodeURIComponent(full), "_blank"); });
        mk("↗", "在對方電腦上開啟", async () => { try { await post("/api/fs/open", { path: full }); toast("已在對方電腦開啟"); } catch (err) { toast(err.message); } });
        mk("✎", "重新命名", async () => { const n = prompt("新名稱", e.name); if (n && n !== e.name) { try { await post("/api/fs/rename", { path: full, name: n }); fsLoad(d.path); } catch (err) { toast(err.message); } } });
        mk("🗑", "刪除", async () => { if (confirm(`確定要刪除「${e.name}」嗎？${e.is_dir ? "（整個資料夾）" : ""}`)) { try { await post("/api/fs/delete", { path: full }); fsLoad(d.path); } catch (err) { toast(err.message); } } });
        ul.appendChild(li);
      }
    } catch (e) { toast("無法讀取：" + e.message); }
  }
  $("#fsUp").onclick = () => fsLoad($("#fsUp").dataset.parent);
  $("#fsGo").onclick = () => fsLoad($("#fsPath").value);
  $("#fsPath").addEventListener("keydown", (e) => { if (e.key === "Enter") fsLoad($("#fsPath").value); });
  $("#fsRefresh").onclick = () => fsLoad(state.fsPath);
  $("#fsMkdir").onclick = async () => { const n = prompt("資料夾名稱"); if (n) { try { await post("/api/fs/mkdir", { path: state.fsPath, name: n }); fsLoad(state.fsPath); } catch (e) { toast(e.message); } } };
  $("#fsUpload").addEventListener("change", (e) => { upload(e.target.files, state.fsPath); e.target.value = ""; });
  function upload(fileList, dir) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const fd = new FormData(); let total = 0;
    for (const f of files) { fd.append("file", f, f.name); total += f.size; }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/fs/upload?dir=" + encodeURIComponent(dir || ""));
    const prog = $("#fsProgress"); prog.classList.remove("hidden");
    xhr.upload.onprogress = (ev) => { const pct = ev.lengthComputable ? Math.round(ev.loaded / ev.total * 100) : 0; prog.firstElementChild.style.width = pct + "%"; prog.lastElementChild.textContent = `上傳中 ${pct}% (${fmtSize(ev.loaded)} / ${fmtSize(total)})`; };
    xhr.onload = () => { prog.classList.add("hidden"); if (xhr.status === 200) { toast(`已傳送 ${files.length} 個檔案`); if (state.panel === "files") fsLoad(state.fsPath); } else toast("上傳失敗：" + xhr.status); };
    xhr.onerror = () => { prog.classList.add("hidden"); toast("上傳失敗"); };
    xhr.send(fd);
    toast(`開始上傳 ${files.length} 個檔案（${fmtSize(total)}）`);
  }
  // drag & drop onto the screen -> receive folder
  let dragDepth = 0;
  stage.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("#dropHint").classList.remove("hidden"); });
  stage.addEventListener("dragover", (e) => e.preventDefault());
  stage.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropHint").classList.add("hidden"); } });
  stage.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("#dropHint").classList.add("hidden"); upload(e.dataTransfer.files, ""); });
  // paste a file (screenshot) into the page -> upload
  document.addEventListener("paste", (e) => { if (inInput()) return; const files = Array.from(e.clipboardData?.files || []); if (files.length) upload(files, ""); });

  // ---------------------------------------------------------------- chat
  function addChat(m) {
    const log = $("#chatLog"); const d = document.createElement("div"); d.className = "m";
    d.innerHTML = `<span class="f"></span><span class="x"></span><span class="tm"></span>`;
    d.querySelector(".f").textContent = m.from; d.querySelector(".x").textContent = m.text; d.querySelector(".tm").textContent = m.time;
    log.appendChild(d); log.scrollTop = log.scrollHeight;
    if (state.panel !== "chat" && m.from !== state.opts.name) { state.unread++; const b = $("#chatBadge"); b.textContent = state.unread; b.classList.remove("hidden"); }
  }
  const sendChat = () => { const t = $("#chatInput").value.trim(); if (t) { send({ t: "chat", text: t }); $("#chatInput").value = ""; } };
  $("#chatSend").onclick = sendChat;
  $("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

  // ---------------------------------------------------------------- system
  async function loadSys() {
    try {
      const s = await api("/api/sysinfo"); const rows = [];
      rows.push(["主機", s.hostname], ["作業系統", `${s.os} (${s.machine})`], ["使用者", s.user], ["對方時間", s.time]);
      if (s.cpu_percent !== undefined) rows.push(["CPU", `${s.cpu_percent}% / ${s.cpu_count} 核心`]);
      if (s.mem_total) rows.push(["記憶體", `${s.mem_percent}%  (${fmtSize(s.mem_used)} / ${fmtSize(s.mem_total)})`]);
      if (s.uptime) rows.push(["開機時間", `${Math.floor(s.uptime / 3600)} 小時 ${Math.floor(s.uptime % 3600 / 60)} 分`]);
      for (const d of s.disks || []) rows.push(["磁碟 " + d.mount, `${d.percent}%  (${fmtSize(d.used)} / ${fmtSize(d.total)})`]);
      if (s.battery) rows.push(["電池", `${s.battery.percent}% ${s.battery.plugged ? "(充電中)" : ""}`]);
      rows.push(["連線中", (s.viewers || []).map((v) => `${v.name} (${v.ip})`).join(", ") || "-"]);
      $("#sysTable").innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${String(v).replace(/</g, "&lt;")}</td></tr>`).join("");
    } catch (e) { toast(e.message); }
  }
  async function loadAudit() {
    try { const d = await api("/api/audit"); $("#auditOut").textContent = d.audit.slice(-40).map((a) => `${a.time} ${a.kind} ${a.name || ""} ${a.ip || ""} ${a.path || a.action || a.cmd || ""}`).join("\n") || "（無）"; } catch (e) {}
  }
  setInterval(() => { if (state.panel === "system") loadSys(); }, 4000);
  $$("[data-power]").forEach((b) => b.addEventListener("click", async () => {
    const a = b.dataset.power; const names = { lock: "鎖定", sleep: "睡眠", logout: "登出", restart: "重新開機", shutdown: "關機" };
    if (!confirm(`確定要讓對方電腦「${names[a]}」嗎？`)) return;
    try { const r = await post("/api/power", { action: a }); toast("已送出：" + names[a] + (r.result && r.result.startsWith("demo") ? "（示範模式未執行）" : "")); } catch (e) { toast(e.message); }
  }));
  $("#btnShot").onclick = () => { canvas.toBlob((blob) => { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `screenshot-${new Date().toISOString().replace(/[:.]/g, "-")}.png`; a.click(); }, "image/png"); };
  $("#btnRefresh").onclick = () => send({ t: "refresh" });
  $("#btnPause").onclick = () => { state.paused = !state.paused; $("#btnPause").textContent = state.paused ? "▶ 繼續串流" : "⏸ 暫停串流"; send({ t: "settings", paused: state.paused }); };
  const runShell = async () => { const cmd = $("#shellCmd").value.trim(); if (!cmd) return; $("#shellOut").textContent = "執行中…"; try { const r = await post("/api/shell", { cmd }); $("#shellOut").textContent = r.output || `(exit ${r.code})`; } catch (e) { $("#shellOut").textContent = e.message; } };
  $("#shellRun").onclick = runShell;
  $("#shellCmd").addEventListener("keydown", (e) => { if (e.key === "Enter") runShell(); });

  // ---------------------------------------------------------------- devices / wol
  async function wol(mac) { try { await post("/api/wol", { mac }); toast("已送出喚醒封包：" + mac); } catch (e) { toast(e.message); } }
  $("#wolSend").onclick = () => wol($("#wolMac").value.trim());

  // ---------------------------------------------------------------- settings
  function applyOpts() {
    const o = state.opts;
    $("#optInvertScroll").checked = !!o.invertScroll;
    $("#optShowCursor").checked = o.showCursor !== false; o.showCursor = o.showCursor !== false;
    $("#optFit").checked = o.fit !== false; stage.classList.toggle("noFit", o.fit === false);
    $("#optViewOnly").checked = !!o.viewOnly; state.viewOnly = !!o.viewOnly; stage.classList.toggle("viewOnly", state.viewOnly);
    for (const id of ["selQuality", "selFps", "selScale"]) if (o[id]) $("#" + id).value = o[id];
  }
  $("#optInvertScroll").onchange = (e) => { state.opts.invertScroll = e.target.checked; saveOpts(); };
  $("#optShowCursor").onchange = (e) => { state.opts.showCursor = e.target.checked; saveOpts(); if (!e.target.checked) cursorEl.style.display = "none"; };
  $("#optFit").onchange = (e) => { state.opts.fit = e.target.checked; saveOpts(); stage.classList.toggle("noFit", !e.target.checked); };
  $("#optViewOnly").onchange = (e) => { state.opts.viewOnly = e.target.checked; state.viewOnly = e.target.checked; saveOpts(); stage.classList.toggle("viewOnly", state.viewOnly); if (state.viewOnly) send({ t: "release_all" }); };
  function renderConn() {
    const i = state.info || {};
    const rows = [["對方名稱", i.name], ["對方系統", i.os], ["版本", i.version], ["擷取方式", i.capture], ["控制方式", i.control], ["網址", location.href]];
    $("#connTable").innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${String(v ?? "").replace(/</g, "&lt;")}</td></tr>`).join("");
  }

  // ---------------------------------------------------------------- boot
  (async () => {
    loadInfo(); pollPeers(); state.peersTimer = setInterval(pollPeers, 4000);
    try { await api("/api/session"); enterApp(); } catch (e) { showLogin(); }
  })();
})();
