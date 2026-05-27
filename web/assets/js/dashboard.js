/* radioman dashboard — vanilla JS, no framework dependencies */

const API = "";
const POLL_MS = 5000;

let currentView = "overview";
let pollTimer   = null;
let graphNodes  = [];
let graphEdges  = [];
let graphAnim   = null;

// ── Theme ─────────────────────────────────────────────────────────────────────
const root = document.getElementById("rmRoot");
const themeBtn = document.getElementById("rmThemeBtn");

function getTheme() {
  return localStorage.getItem("rm-theme") || "dark";
}
function setTheme(t) {
  root.setAttribute("data-aap-theme", t);
  document.documentElement.setAttribute("data-aap-theme", t);
  localStorage.setItem("rm-theme", t);
  themeBtn.textContent = t === "dark" ? "☀︎" : "☾";
}
setTheme(getTheme());
themeBtn.addEventListener("click", () => {
  setTheme(getTheme() === "dark" ? "light" : "dark");
});

// ── Navigation ────────────────────────────────────────────────────────────────
document.getElementById("rmNav").addEventListener("click", e => {
  const btn = e.target.closest(".rm-nav-btn");
  if (!btn) return;
  document.querySelectorAll(".rm-nav-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  currentView = btn.dataset.view;
  renderView();
});

// ── Fetch helpers ─────────────────────────────────────────────────────────────
async function get(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}
async function post(path, body = {}) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

function setStatus(ok) {
  const dot = document.getElementById("rmStatusDot");
  dot.className = "rm-status-dot " + (ok ? "ok" : "err");
}

// ── Poll loop ─────────────────────────────────────────────────────────────────
async function poll() {
  try {
    const fetches = [get("/api/status"), fetchViewData()];
    if (currentView === "overview") fetches.push(fetchXpltStatus());
    const [status, data, xplt] = await Promise.all(fetches);
    setStatus(true);
    document.getElementById("rmLoading")?.remove();
    updateUptime(status.personality?.uptime_seconds || 0);
    // Don't re-render if the user is actively typing or has content in the
    // XPLT pairing fields — prevents inputs from being wiped mid-entry.
    const active = document.activeElement;
    const userTyping = active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
    const pairCode = document.getElementById("rmPairCode");
    const pairName = document.getElementById("rmPairName");
    const userHasInput = pairCode?.value || pairName?.value;
    if (!userTyping && !userHasInput) {
      renderMain(status, data, xplt || null);
    }
  } catch (e) {
    setStatus(false);
    console.error("Poll error:", e);
  }
}

async function fetchViewData() {
  switch (currentView) {
    case "networks":  return get("/api/networks");
    case "clients":   return get("/api/clients");
    case "captures":  return get("/api/captures");
    case "graph":     return get("/api/graph");
    case "hosts":     return get("/api/hosts");
    case "log":       return get("/api/events?limit=100");
    case "ignore":    return get("/api/ignore");
    case "stats":     return Promise.all([get("/api/networks"), get("/api/stats")]);
    case "ai":        return get("/api/ai/status");
    case "overview":  return Promise.all([get("/api/networks"), get("/api/events?limit=3")]);
    default:          return null;
  }
}

async function fetchXpltStatus() {
  try { return await get("/api/xplt/status"); }
  catch { return null; }
}

function updateUptime(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  document.getElementById("rmUptime").textContent =
    `uptime: ${pad(h)}:${pad(m)}:${pad(s)}`;
}
function pad(n) { return String(n).padStart(2, "0"); }

function renderView() {
  poll();
}

function renderMain(status, data, xplt = null) {
  const main = document.getElementById("rmMain");
  switch (currentView) {
    case "overview": {
      const [recentNets, recentEvents] = Array.isArray(data) ? data : [[], []];
      main.innerHTML = viewOverview(status, xplt, recentNets || [], recentEvents || []);
      attachXpltHandler(); attachScanToggle();
      break;
    }
    case "networks":  main.innerHTML = viewNetworks(data || []); attachIgnoreHandlers(); break;
    case "clients":   main.innerHTML = viewClients(data || []); break;
    case "captures":  main.innerHTML = viewCaptures(data || []); attachCrackHandlers(); break;
    case "graph":     main.innerHTML = viewGraph(); drawGraph(data || { nodes: [], edges: [] }); break;
    case "hosts":     main.innerHTML = viewHosts(data || [], status); attachScanHandler(); break;
    case "log":       main.innerHTML = viewLog(data || []); break;
    case "ignore":    main.innerHTML = viewIgnore(data || []); attachIgnoreHandlers(); break;
    case "stats": {
      const [networks, statsData] = Array.isArray(data) ? data : [[], {}];
      main.innerHTML = viewStats(networks || []);
      drawAllCharts(networks || [], statsData || {});
      attachRssiClickHandlers();
      break;
    }
    case "ai":
      main.innerHTML = viewAI(data || {});
      attachAIHandlers();
      break;
  }
}

// ── Overview ──────────────────────────────────────────────────────────────────
function viewOverview(status, xplt, recentNets = [], recentEvents = []) {
  const p       = status.personality || {};
  const b       = status.battery     || {};
  const s       = status.stats       || {};
  const cq      = status.crack_queue || {};
  const scanning = status.scanning   ?? false;

  const pct  = b.percent ?? -1;
  const chrg = b.charging ? " <span class='rm-charging'>⚡ charging</span>" : "";
  const hearts = makeHearts(pct);

  return `
    <div class="rm-tile-row">
      <div class="dash-kpi-card rm-scan-tile">
        <div class="dash-kpi-label">Status</div>
        <div class="rm-scan-indicator ${scanning ? "rm-scan-indicator--on" : "rm-scan-indicator--off"}">
          <div class="rm-scan-indicator-dot"></div>
          <span class="rm-mono">${scanning ? "Scanning" : "Idle"}</span>
        </div>
        <div class="dash-kpi-sub rm-status-msg">${esc(p.message || "standing by")}</div>
        <div class="rm-battery rm-battery-sm">
          <span class="rm-hearts">${hearts}</span>
          <span class="rm-mono rm-muted">${pct >= 0 ? pct + "%" : "—"}</span>
          ${chrg}
        </div>
        <button id="rmScanToggleBtn"
          class="rm-btn ${scanning ? "rm-btn-danger" : "rm-btn-primary"} rm-scan-btn"
          style="width:100%; margin-top:auto">
          ${scanning ? "⏹ Stop" : "▶ Start Scanning"}
        </button>
      </div>
      ${kpi("Networks", s.networks ?? 0, "teal")}
      ${kpi("Clients", s.clients ?? 0, "teal")}
      ${kpi("Handshakes", s.captures ?? 0, s.captures > 0 ? "ok" : "")}
      ${kpi("Cracked", s.cracked ?? 0, s.cracked > 0 ? "ok" : "")}
      ${kpi("Queue", cq.queued ?? 0, cq.queued > 0 ? "warn" : "")}
    </div>
    <div class="dash-panels">
      ${recentNetworksPanel(recentNets)}
      ${recentEventsPanel(recentEvents)}
    </div>
    ${xpltPanel(xplt)}`;
}

function recentNetworksPanel(nets = []) {
  const top5 = nets.slice(0, 3);
  const body = top5.length === 0
    ? `<div class="dash-panel-body rm-empty"><div class="rm-empty-icon">📶</div><p>No networks yet</p></div>`
    : `<table class="dash-table">
        <thead><tr><th>SSID</th><th>CH</th><th>Signal</th><th>Security</th></tr></thead>
        <tbody>
          ${top5.map(r => `
            <tr>
              <td class="rm-table-ssid">${esc(r.ssid || "—")}</td>
              <td>${r.channel ?? "—"}</td>
              <td>${rssiCell(r.rssi)}</td>
              <td>${secBadge(r.security)}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  return `
    <div class="dash-panel">
      <div class="dash-panel-header">
        <h3>Recent Networks</h3>
        <button class="rm-nav-btn" onclick="navigate('networks')">View all →</button>
      </div>
      ${body}
    </div>`;
}

function recentEventsPanel(events = []) {
  const body = events.length === 0
    ? `<div class="dash-panel-body rm-empty"><div class="rm-empty-icon">📋</div><p>No events yet</p></div>`
    : `<div class="rm-log-list">
        ${events.map(e => `
          <div class="rm-log-row">
            <span class="rm-log-ts rm-mono rm-muted">${shortDate(e.ts)}</span>
            <span class="rm-log-level rm-log-level--${esc(e.level || "info")}">${esc(e.level || "")}</span>
            <span class="rm-log-msg">${esc(e.message || "")}</span>
          </div>`).join("")}
      </div>`;
  return `
    <div class="dash-panel">
      <div class="dash-panel-header">
        <h3>Recent Events</h3>
        <button class="rm-nav-btn" onclick="navigate('log')">View all →</button>
      </div>
      ${body}
    </div>`;
}

function xpltPanel(x) {
  if (!x) return "";
  if (!x.enabled) return `
    <div class="dash-panel dash-panel-full rm-xplt-panel">
      <div class="dash-panel-header">
        <h3>XPLT Sync</h3>
        <span class="rm-xplt-badge rm-xplt-off">not paired</span>
      </div>
      <div class="rm-xplt-body">
        <p class="rm-muted" style="margin-bottom:1rem;font-size:0.85rem">
          Open your XPLT account, go to <strong>Integrations → Radioman</strong>,
          and generate a pairing code. Then enter it below.
        </p>
        <div class="rm-ignore-form">
          <input class="rm-ignore-input rm-mono" id="rmPairCode"
                 placeholder="XXXXXXXX" maxlength="9" spellcheck="false"
                 autocomplete="off" autocorrect="off" autocapitalize="characters"
                 style="letter-spacing:2px;text-transform:uppercase" />
          <input class="rm-ignore-input" id="rmPairName"
                 placeholder="Device name (e.g. radioman-1)" maxlength="80" />
          <button class="rm-btn rm-btn-primary" id="rmPairBtn">Connect to XPLT</button>
        </div>
        <div id="rmPairError" style="color:var(--rm-red);font-size:0.8rem;margin-top:0.5rem;display:none"></div>
      </div>
    </div>`;

  const online   = !x.last_error;
  const lastSync = x.last_sync
    ? new Date(x.last_sync * 1000).toLocaleString(undefined, {
        month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      })
    : "never";

  return `
    <div class="dash-panel dash-panel-full rm-xplt-panel">
      <div class="dash-panel-header">
        <h3>XPLT Sync</h3>
        <span class="rm-xplt-badge ${online ? "rm-xplt-ok" : "rm-xplt-err"}">
          ${online ? "connected" : "error"}
        </span>
      </div>
      <div class="rm-xplt-body">
        <div class="rm-xplt-row">
          <span class="rm-muted">Last sync</span>
          <span class="rm-mono">${lastSync}</span>
        </div>
        <div class="rm-xplt-row">
          <span class="rm-muted">Pending</span>
          <span class="rm-mono ${x.pending > 0 ? "rm-amber" : ""}">${x.pending} record${x.pending !== 1 ? "s" : ""}</span>
        </div>
        <div class="rm-xplt-row">
          <span class="rm-muted">Total pushed</span>
          <span class="rm-mono rm-teal">${x.total_pushed}</span>
        </div>
        ${x.last_error ? `<div class="rm-xplt-row rm-red rm-muted">${esc(x.last_error)}</div>` : ""}
        <button class="rm-btn rm-btn-primary" id="rmXpltSyncBtn" style="margin-top:0.5rem">
          Sync now
        </button>
      </div>
    </div>`;
}

function attachXpltHandler() {
  // Sync button (connected state)
  const syncBtn = document.getElementById("rmXpltSyncBtn");
  if (syncBtn) {
    syncBtn.addEventListener("click", async () => {
      syncBtn.textContent = "Syncing…";
      syncBtn.disabled = true;
      try {
        await post("/api/xplt/sync");
        setTimeout(poll, 3000);
      } catch (e) {
        syncBtn.textContent = "Error";
      }
      setTimeout(() => { syncBtn.textContent = "Sync now"; syncBtn.disabled = false; }, 4000);
    });
  }

  // Pair button (not-paired state)
  const pairBtn = document.getElementById("rmPairBtn");
  if (pairBtn) {
    pairBtn.addEventListener("click", async () => {
      const codeEl = document.getElementById("rmPairCode");
      const nameEl = document.getElementById("rmPairName");
      const errEl  = document.getElementById("rmPairError");
      const code   = (codeEl?.value || "").replace(/[\s\-]/g, "").toUpperCase();
      const name   = (nameEl?.value || "").trim() || "radioman";

      if (code.length !== 8) {
        errEl.textContent = "Enter the 8-character code from your XPLT account";
        errEl.style.display = "";
        codeEl?.focus();
        return;
      }
      errEl.style.display = "none";
      pairBtn.textContent = "Connecting…";
      pairBtn.disabled = true;

      try {
        const result = await post("/api/xplt/pair", { code, device_name: name });
        if (result.error) {
          errEl.textContent = result.error;
          errEl.style.display = "";
          pairBtn.textContent = "Connect to XPLT";
          pairBtn.disabled = false;
        } else {
          pairBtn.textContent = "Paired!";
          setTimeout(poll, 1500);
        }
      } catch (e) {
        errEl.textContent = "Network error — check your connection and try again";
        errEl.style.display = "";
        pairBtn.textContent = "Connect to XPLT";
        pairBtn.disabled = false;
      }
    });

    // Allow typing XXXX XXXX with auto-space after 4 chars
    const codeEl = document.getElementById("rmPairCode");
    if (codeEl) {
      codeEl.addEventListener("input", () => {
        let v = codeEl.value.replace(/[^A-Za-z0-9]/g, "").toUpperCase().slice(0, 8);
        codeEl.value = v.length > 4 ? v.slice(0, 4) + " " + v.slice(4) : v;
      });
    }
  }
}

function attachScanToggle() {
  const btn = document.getElementById("rmScanToggleBtn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const starting = btn.textContent.includes("Start");
    btn.textContent = starting ? "Starting…" : "Stopping…";
    btn.disabled = true;
    try {
      await post(starting ? "/api/scan/start" : "/api/scan/stop");
      setTimeout(poll, 1000);
    } catch (e) {
      btn.textContent = starting ? "▶ Start Scanning" : "⏹ Stop Scanning";
      btn.disabled = false;
    }
  });
}

function kpi(label, value, mod = "") {
  return `
    <div class="dash-kpi-card">
      <div class="dash-kpi-label">${label}</div>
      <div class="dash-kpi-value${mod ? " " + mod : ""}">${value}</div>
    </div>`;
}

function pct2bar(v) {
  const p = Math.round(v * 100);
  return `${p}%`;
}

// ── Networks ──────────────────────────────────────────────────────────────────
function viewNetworks(rows) {
  if (!rows.length) return empty("📶", "No networks discovered yet");
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} network${rows.length !== 1 ? "s" : ""} discovered</span>
    </div>
    <div class="dash-panel dash-panel-full">
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr>
            <th>SSID</th><th>BSSID</th><th>CH</th>
            <th>Signal</th><th>Security</th><th>Clients</th><th>Last Seen</th><th></th>
          </tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td class="rm-table-ssid">${esc(r.ssid || "—")}</td>
                <td class="rm-table-bssid rm-mono">${esc(r.bssid)}</td>
                <td>${r.channel ?? "—"}</td>
                <td>${rssiCell(r.rssi)}</td>
                <td>${secBadge(r.security)}</td>
                <td>${r.clients ?? 0}</td>
                <td class="rm-muted">${shortDate(r.last_seen)}</td>
                <td><button class="rm-ignore-btn" data-bssid="${esc(r.bssid)}" title="Add to ignore list">Ignore</button></td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ── Clients ───────────────────────────────────────────────────────────────────
function viewClients(rows) {
  if (!rows.length) return empty("📱", "No clients discovered yet");
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} client${rows.length !== 1 ? "s" : ""} seen</span>
    </div>
    <div class="dash-panel dash-panel-full">
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr>
            <th>MAC</th><th>Associated AP</th><th>Vendor</th><th>Signal</th><th>Last Seen</th>
          </tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td class="rm-mono">${esc(r.mac)}</td>
                <td class="rm-mono rm-muted">${esc(r.bssid || "—")}</td>
                <td>${esc(r.vendor || "—")}</td>
                <td>${rssiCell(r.rssi)}</td>
                <td class="rm-muted">${shortDate(r.last_seen)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ── Captures ──────────────────────────────────────────────────────────────────
function viewCaptures(rows) {
  if (!rows.length) return empty("🔐", "No handshakes captured yet");
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} capture${rows.length !== 1 ? "s" : ""}</span>
    </div>
    <div class="dash-panel dash-panel-full">
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr>
            <th>SSID</th><th>BSSID</th><th>Type</th>
            <th>Captured</th><th>Status</th><th>Actions</th>
          </tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td class="rm-table-ssid">${esc(r.ssid || "—")}</td>
                <td class="rm-mono rm-table-bssid">${esc(r.bssid || "—")}</td>
                <td><span class="rm-cap-type">${esc(r.type || "—")}</span></td>
                <td class="rm-muted">${shortDate(r.captured_at)}</td>
                <td>${r.cracked
                  ? `<span class="rm-crack-badge-ok">✓ ${esc(r.password || "found")}</span>`
                  : `<span class="rm-muted">pending</span>`}</td>
                <td class="rm-cap-actions">
                  ${!r.cracked
                    ? `<button class="rm-crack-btn" data-id="${r.id}">Crack</button>`
                    : ""}
                  <a class="rm-btn rm-cap-dl-btn"
                     href="/api/captures/${r.id}/download"
                     download title="Download .pcapng">↓ pcapng</a>
                </td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function attachCrackHandlers() {
  document.querySelectorAll(".rm-crack-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      btn.textContent = "Queued…";
      btn.disabled = true;
      try {
        await post(`/api/crack/${id}`);
      } catch (e) {
        btn.textContent = "Error";
      }
    });
  });
}

// ── Graph ─────────────────────────────────────────────────────────────────────
function viewGraph() {
  return `
    <div class="dash-panel dash-panel-full">
      <div class="dash-panel-header"><h3>Network Graph</h3></div>
      <div class="rm-graph-wrap">
        <canvas id="rmGraphCanvas"></canvas>
        <div class="rm-graph-legend">
          <span class="rm-legend-ap">Access Points</span>
          <span class="rm-legend-client">Clients</span>
        </div>
      </div>
    </div>`;
}

function drawGraph(data) {
  graphNodes = data.nodes || [];
  graphEdges = data.edges || [];

  const canvas = document.getElementById("rmGraphCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const W = canvas.offsetWidth || 800;
  const H = canvas.offsetHeight || 480;
  canvas.width  = W;
  canvas.height = H;

  const theme = getTheme();
  const bgColor    = theme === "dark" ? "#0b1424" : "#f5f8fc";
  const apColor    = "#5ee1c8";
  const cliColor   = "#fbbf24";
  const edgeColor  = theme === "dark" ? "rgba(148,163,184,0.2)" : "rgba(15,23,42,0.12)";
  const textColor  = theme === "dark" ? "#94a3b8" : "#475569";

  const positions = {};
  graphNodes.forEach((n, i) => {
    const angle = (i / graphNodes.length) * Math.PI * 2;
    const r     = Math.min(W, H) * 0.35;
    positions[n.id] = {
      x: W / 2 + Math.cos(angle) * r * (n.type === "ap" ? 0.6 : 1),
      y: H / 2 + Math.sin(angle) * r * (n.type === "ap" ? 0.6 : 1),
      vx: 0, vy: 0,
    };
  });

  function tick() {
    graphNodes.forEach(a => {
      graphNodes.forEach(b => {
        if (a.id === b.id) return;
        const pa = positions[a.id], pb = positions[b.id];
        const dx = pa.x - pb.x, dy = pa.y - pb.y;
        const d  = Math.sqrt(dx * dx + dy * dy) || 1;
        const f  = 800 / (d * d);
        pa.vx += dx * f; pa.vy += dy * f;
      });
    });
    graphEdges.forEach(e => {
      const pa = positions[e.source], pb = positions[e.target];
      if (!pa || !pb) return;
      const dx = pb.x - pa.x, dy = pb.y - pa.y;
      const d  = Math.sqrt(dx * dx + dy * dy) || 1;
      const f  = (d - 80) * 0.015;
      pa.vx += dx * f; pa.vy += dy * f;
      pb.vx -= dx * f; pb.vy -= dy * f;
    });
    graphNodes.forEach(n => {
      const p = positions[n.id];
      p.vx *= 0.85; p.vy *= 0.85;
      p.x = Math.max(20, Math.min(W - 20, p.x + p.vx));
      p.y = Math.max(20, Math.min(H - 20, p.y + p.vy));
    });
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = edgeColor;
    ctx.lineWidth   = 1;
    graphEdges.forEach(e => {
      const pa = positions[e.source], pb = positions[e.target];
      if (!pa || !pb) return;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    });

    graphNodes.forEach(n => {
      const p = positions[n.id];
      if (!p) return;
      const r = n.type === "ap" ? 9 : 6;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle   = n.type === "ap" ? apColor : cliColor;
      ctx.shadowColor = n.type === "ap" ? apColor : cliColor;
      ctx.shadowBlur  = 8;
      ctx.fill();
      ctx.shadowBlur  = 0;

      if (n.type === "ap" || graphNodes.length < 60) {
        ctx.fillStyle  = textColor;
        ctx.font       = "10px ui-monospace,monospace";
        ctx.textAlign  = "center";
        ctx.fillText((n.label || n.id).slice(0, 18), p.x, p.y + r + 12);
      }
    });
  }

  let steps = 0;
  function animate() {
    tick();
    draw();
    steps++;
    if (steps < 120 || graphAnim) {
      graphAnim = requestAnimationFrame(animate);
    }
  }
  if (graphAnim) cancelAnimationFrame(graphAnim);
  graphAnim = requestAnimationFrame(animate);
}

// ── LAN Hosts ─────────────────────────────────────────────────────────────────
function viewHosts(rows, _status) {
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} host${rows.length !== 1 ? "s" : ""} in ARP table</span>
      <button class="rm-btn rm-btn-primary" id="rmScanBtn">Run nmap scan</button>
    </div>
    ${rows.length
      ? `<div class="dash-panel dash-panel-full">
          <div class="dash-table-scroll">
            <table class="dash-table">
              <thead><tr><th>IP</th><th>MAC</th><th>Vendor</th><th>Method</th></tr></thead>
              <tbody>
                ${rows.map(r => `
                  <tr>
                    <td class="rm-mono">${esc(r.ip || "—")}</td>
                    <td class="rm-mono rm-muted">${esc(r.mac || "—")}</td>
                    <td>${esc(r.vendor || "—")}</td>
                    <td><span class="rm-muted">${esc(r.method || "arp")}</span></td>
                  </tr>`).join("")}
              </tbody>
            </table>
          </div>
        </div>`
      : empty("🏠", "No LAN hosts in ARP table yet")}`;
}

function attachScanHandler() {
  const btn = document.getElementById("rmScanBtn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.textContent = "Scanning…";
    btn.disabled = true;
    try {
      const hosts = await post("/api/hosts/scan");
      btn.textContent = `Done — ${hosts.length} hosts`;
      setTimeout(poll, 1000);
    } catch (e) {
      btn.textContent = "Error";
    }
    setTimeout(() => { btn.textContent = "Run nmap scan"; btn.disabled = false; }, 3000);
  });
}

// ── Log ───────────────────────────────────────────────────────────────────────
function viewLog(rows) {
  if (!rows.length) return empty("📋", "No events logged yet");
  return `
    <div class="dash-panel dash-panel-full">
      <div class="dash-panel-header"><h3>Event Log</h3>
        <span class="rm-muted">${rows.length} entries</span>
      </div>
      <div class="rm-log-list">
        ${rows.map(r => `
          <div class="rm-log-row">
            <span class="rm-log-ts">${shortDate(r.ts)}</span>
            <span class="rm-log-lvl rm-log-lvl-${esc(r.level)}">${esc(r.level)}</span>
            <span>${esc(r.message)}</span>
          </div>`).join("")}
      </div>
    </div>`;
}

// ── Ignore list ───────────────────────────────────────────────────────────────
function viewIgnore(rows) {
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} BSSID${rows.length !== 1 ? "s" : ""} ignored</span>
    </div>
    <div class="dash-panel dash-panel-full">
      <div class="dash-panel-header">
        <h3>Add BSSID to Ignore List</h3>
      </div>
      <div class="rm-ignore-form">
        <input class="rm-ignore-input rm-mono" id="rmIgnoreBssid"
               placeholder="AA:BB:CC:DD:EE:FF" maxlength="17" spellcheck="false" />
        <input class="rm-ignore-input" id="rmIgnoreNote"
               placeholder="Note (optional, e.g. home router)" maxlength="80" />
        <button class="rm-btn rm-btn-primary" id="rmIgnoreAddBtn">Add</button>
      </div>
    </div>
    ${rows.length ? `
    <div class="dash-panel dash-panel-full" style="margin-top:1rem">
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr><th>BSSID</th><th>Note</th><th>Added (UTC)</th><th></th></tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td class="rm-mono">${esc(r.bssid)}</td>
                <td class="rm-muted">${esc(r.note || "—")}</td>
                <td class="rm-muted">${(r.added || "").slice(0, 19).replace("T", " ")}</td>
                <td><button class="rm-unignore-btn rm-crack-btn"
                            data-bssid="${esc(r.bssid)}">Remove</button></td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>` : empty("", "No BSSIDs ignored yet")}`;
}

function attachIgnoreHandlers() {
  // Add button in ignore view
  const addBtn = document.getElementById("rmIgnoreAddBtn");
  if (addBtn) {
    addBtn.addEventListener("click", async () => {
      const bssidEl = document.getElementById("rmIgnoreBssid");
      const noteEl  = document.getElementById("rmIgnoreNote");
      const bssid   = (bssidEl?.value || "").trim().toUpperCase();
      if (!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(bssid)) {
        bssidEl.style.borderColor = "var(--rm-red)";
        bssidEl.focus();
        return;
      }
      bssidEl.style.borderColor = "";
      addBtn.textContent = "Adding…";
      addBtn.disabled = true;
      try {
        await post("/api/ignore", { bssid, note: noteEl?.value || "" });
        bssidEl.value = "";
        if (noteEl) noteEl.value = "";
        poll();
      } catch (e) {
        addBtn.textContent = "Error";
      }
      addBtn.textContent = "Add";
      addBtn.disabled = false;
    });
  }

  // Remove buttons in ignore view
  document.querySelectorAll(".rm-unignore-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const bssid = btn.dataset.bssid;
      btn.textContent = "Removing…";
      btn.disabled = true;
      try {
        await del(`/api/ignore/${encodeURIComponent(bssid)}`);
        poll();
      } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
      }
    });
  });

  // Ignore buttons in networks view
  document.querySelectorAll(".rm-ignore-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const bssid = btn.dataset.bssid;
      btn.textContent = "Ignoring…";
      btn.disabled = true;
      try {
        await post("/api/ignore", { bssid, note: "" });
        btn.textContent = "Ignored";
        btn.classList.add("rm-ignored");
      } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
      }
    });
  });
}

async function del(path) {
  const r = await fetch(API + path, { method: "DELETE" });
  return r.json();
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function empty(icon, msg) {
  return `<div class="rm-empty"><div class="rm-empty-icon">${icon}</div><p>${msg}</p></div>`;
}

function makeHearts(pct, total = 5) {
  if (pct < 0) return "♡".repeat(total);
  const filled = Math.round((pct / 100) * total);
  return "♥".repeat(filled) + "♡".repeat(total - filled);
}

function rssiCell(rssi) {
  const v = rssi ?? 0;
  const pct = Math.max(0, Math.min(100, ((v + 100) / 70) * 100));
  return `<div class="rm-rssi-wrap">
    <div class="rm-rssi-bar"><div class="rm-rssi-fill" style="width:${pct}%"></div></div>
    <span class="rm-rssi-val">${v} dBm</span>
  </div>`;
}

function secBadge(sec) {
  const s = (sec || "").toUpperCase();
  let cls = "rm-sec-other";
  if (s === "WPA3") cls = "rm-sec-wpa3";
  else if (s === "WPA2") cls = "rm-sec-wpa2";
  else if (s === "WPA")  cls = "rm-sec-wpa";
  else if (s === "WEP")  cls = "rm-sec-wep";
  else if (s === "OPEN") cls = "rm-sec-open";
  return `<span class="rm-sec ${cls}">${esc(sec || "?")}</span>`;
}

function shortDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso + "Z").toLocaleString(undefined, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch {
    return iso.slice(0, 19).replace("T", " ");
  }
}

function navigate(view) {
  currentView = view;
  document.querySelectorAll(".rm-nav-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.view === view);
  });
  renderView();
}

// ── Stats view ────────────────────────────────────────────────────────────────

const CHART_COLORS = [
  "#5ee1c8","#fbbf24","#a78bfa","#f472b6","#34d399",
  "#60a5fa","#fb923c","#e879f9","#4ade80","#f87171",
  "#38bdf8","#facc15",
];

const SEC_COLORS = {
  WPA3: "#34d399", WPA2: "#5ee1c8", WPA: "#fbbf24",
  WEP: "#f87171", OPEN: "#fb923c", UNKNOWN: "#64748b",
};

function viewStats(networks) {
  const total = networks.length;
  const has24  = networks.some(n => n.channel >= 1  && n.channel <= 14);
  const has5   = networks.some(n => n.channel >= 36);
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${total} network${total !== 1 ? "s" : ""} in database</span>
      <span class="rm-muted rm-mono" style="font-size:0.72rem">click a row in Networks to see RSSI history</span>
    </div>
    <div class="rm-stats-grid">
      ${has24 ? `
      <div class="dash-panel rm-chart-panel rm-chart-full" id="rmChartChannel24Wrap">
        <div class="dash-panel-header"><h3>2.4 GHz Channel Analyzer</h3>
          <span class="rm-muted" style="font-size:0.72rem">WiFiman-style — curves show signal per AP</span>
        </div>
        <div class="rm-chart-wrap">
          <canvas id="rmChannelChart24" class="rm-chart-canvas rm-chart-tall"></canvas>
          <div class="rm-chart-legend" id="rmChannelLegend24"></div>
        </div>
      </div>` : ""}
      ${has5 ? `
      <div class="dash-panel rm-chart-panel rm-chart-full">
        <div class="dash-panel-header"><h3>5 GHz Channel Analyzer</h3></div>
        <div class="rm-chart-wrap">
          <canvas id="rmChannelChart5" class="rm-chart-canvas rm-chart-tall"></canvas>
          <div class="rm-chart-legend" id="rmChannelLegend5"></div>
        </div>
      </div>` : ""}
      <div class="dash-panel rm-chart-panel">
        <div class="dash-panel-header"><h3>Security Breakdown</h3></div>
        <div class="rm-chart-wrap">
          <canvas id="rmSecChart" class="rm-chart-canvas"></canvas>
          <div class="rm-chart-legend" id="rmSecLegend"></div>
        </div>
      </div>
      <div class="dash-panel rm-chart-panel">
        <div class="dash-panel-header"><h3>Signal Distribution</h3></div>
        <div class="rm-chart-wrap">
          <canvas id="rmRssiChart" class="rm-chart-canvas"></canvas>
        </div>
      </div>
      <div class="dash-panel rm-chart-panel rm-chart-full">
        <div class="dash-panel-header"><h3>Top Vendors</h3></div>
        <div class="rm-chart-wrap">
          <canvas id="rmVendorChart" class="rm-chart-canvas"></canvas>
        </div>
      </div>
    </div>`;
}

function drawAllCharts(networks, statsData) {
  const theme   = getTheme();
  const isDark  = theme === "dark";
  const textClr = isDark ? "#94a3b8" : "#475569";
  const lineClr = isDark ? "rgba(148,163,184,0.12)" : "rgba(15,23,42,0.08)";

  const ctx24  = document.getElementById("rmChannelChart24");
  const ctx5   = document.getElementById("rmChannelChart5");
  const ctxSec = document.getElementById("rmSecChart");
  const ctxRss = document.getElementById("rmRssiChart");
  const ctxVnd = document.getElementById("rmVendorChart");

  const nets24 = networks.filter(n => n.channel >= 1  && n.channel <= 14);
  const nets5  = networks.filter(n => n.channel >= 36);

  if (ctx24 && nets24.length) drawChannelChart(ctx24, nets24, 1, 13, textClr, lineClr, "rmChannelLegend24");
  if (ctx5  && nets5.length)  drawChannelChart(ctx5,  nets5,  36, 165, textClr, lineClr, "rmChannelLegend5");
  if (ctxSec) drawDonutChart(ctxSec, statsData.security || {}, textClr);
  if (ctxRss) drawRssiHistogram(ctxRss, networks, textClr, lineClr);
  if (ctxVnd) drawVendorChart(ctxVnd, statsData.vendors || [], textClr);
}

function _fitCanvas(canvas) {
  const W = canvas.offsetWidth  || 600;
  const H = canvas.offsetHeight || 220;
  canvas.width  = W;
  canvas.height = H;
  return { W, H, ctx: canvas.getContext("2d") };
}

// WiFiman-style channel chart: Gaussian arches per AP
function drawChannelChart(canvas, networks, chMin, chMax, textClr, lineClr, legendId) {
  const { W, H, ctx } = _fitCanvas(canvas);
  const mg = { top: 16, right: 16, bottom: 36, left: 44 };
  const pW = W - mg.left - mg.right;
  const pH = H - mg.top  - mg.bottom;

  ctx.clearRect(0, 0, W, H);

  function chX(ch) { return mg.left + ((ch - chMin) / (chMax - chMin)) * pW; }
  function rssiY(rssi) {
    const norm = Math.max(0, Math.min(1, (rssi + 100) / 70));
    return mg.top + pH - norm * pH;
  }

  // Grid lines at RSSI -40, -60, -80
  ctx.strokeStyle = lineClr;
  ctx.lineWidth   = 1;
  [-40, -60, -80].forEach(r => {
    const y = rssiY(r);
    ctx.beginPath(); ctx.moveTo(mg.left, y); ctx.lineTo(mg.left + pW, y); ctx.stroke();
    ctx.fillStyle  = textClr;
    ctx.font       = "10px ui-monospace,monospace";
    ctx.textAlign  = "right";
    ctx.fillText(`${r}`, mg.left - 6, y + 4);
  });

  // Baseline
  ctx.strokeStyle = lineClr;
  ctx.beginPath(); ctx.moveTo(mg.left, mg.top + pH); ctx.lineTo(mg.left + pW, mg.top + pH); ctx.stroke();

  // Channel labels on X axis
  const channelStep = chMax - chMin <= 14 ? 1 : 4;
  for (let ch = chMin; ch <= chMax; ch += channelStep) {
    const x = chX(ch);
    ctx.fillStyle = textClr;
    ctx.font      = "10px ui-monospace,monospace";
    ctx.textAlign = "center";
    ctx.fillText(String(ch), x, mg.top + pH + 16);
  }

  // Gaussian arch per network
  const sigma = chMax <= 14 ? 1.5 : 3;
  const legendEl = document.getElementById(legendId);
  if (legendEl) legendEl.innerHTML = "";

  networks.forEach((net, i) => {
    const color = CHART_COLORS[i % CHART_COLORS.length];
    const amp   = Math.max(0.05, Math.min(1, (net.rssi + 100) / 70));

    ctx.beginPath();
    let first = true;
    for (let px = mg.left; px <= mg.left + pW; px++) {
      const ch = chMin + ((px - mg.left) / pW) * (chMax - chMin);
      const g  = amp * Math.exp(-0.5 * Math.pow((ch - net.channel) / sigma, 2));
      const y  = mg.top + pH - g * pH;
      first ? ctx.moveTo(px, y) : ctx.lineTo(px, y);
      first = false;
    }
    ctx.lineTo(mg.left + pW, mg.top + pH);
    ctx.lineTo(mg.left,      mg.top + pH);
    ctx.closePath();
    ctx.fillStyle   = color + "30";
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth   = 1.5;
    ctx.stroke();

    // Channel center dot
    const cx = chX(net.channel);
    const cy = rssiY(net.rssi);
    ctx.beginPath(); ctx.arc(cx, cy, 3, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();

    if (legendEl) {
      const item = document.createElement("span");
      item.className = "rm-chart-legend-item";
      item.title = `${net.ssid || net.bssid}  ch${net.channel}  ${net.rssi}dBm`;
      item.innerHTML = `<span class="rm-chart-swatch" style="background:${color}"></span>${esc(net.ssid || net.bssid)}`;
      legendEl.appendChild(item);
    }
  });
}

// Security donut chart
function drawDonutChart(canvas, secData, textClr) {
  const { W, H, ctx } = _fitCanvas(canvas);
  const cx = W / 2, cy = H / 2;
  const outerR = Math.min(W, H) * 0.38;
  const innerR = outerR * 0.58;

  ctx.clearRect(0, 0, W, H);

  const entries = Object.entries(secData).filter(([, v]) => v > 0);
  const total   = entries.reduce((s, [, v]) => s + v, 0);

  if (!total) {
    ctx.fillStyle = textClr; ctx.font = "13px ui-monospace,monospace";
    ctx.textAlign = "center"; ctx.fillText("No data", cx, cy);
    return;
  }

  let angle = -Math.PI / 2;
  entries.forEach(([key, count]) => {
    const sweep = (count / total) * Math.PI * 2;
    const color = SEC_COLORS[key.toUpperCase()] || SEC_COLORS.UNKNOWN;
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, angle, angle + sweep);
    ctx.arc(cx, cy, innerR, angle + sweep, angle, true);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    angle += sweep;
  });

  // Center label
  ctx.fillStyle = textClr;
  ctx.font = `bold ${Math.round(outerR * 0.5)}px ui-monospace,monospace`;
  ctx.textAlign = "center";
  ctx.fillText(String(total), cx, cy + 6);
  ctx.font = `${Math.round(outerR * 0.22)}px ui-monospace,monospace`;
  ctx.fillStyle = textClr + "99";
  ctx.fillText("networks", cx, cy + outerR * 0.42);

  // Legend to the right
  const lx = cx + outerR + 16;
  let ly = cy - (entries.length * 18) / 2;
  entries.forEach(([key, count]) => {
    const color = SEC_COLORS[key.toUpperCase()] || SEC_COLORS.UNKNOWN;
    ctx.fillStyle = color;
    ctx.fillRect(lx, ly - 7, 10, 10);
    ctx.fillStyle = textClr;
    ctx.font = "10px ui-monospace,monospace";
    ctx.textAlign = "left";
    ctx.fillText(`${key}  ${count}`, lx + 14, ly + 3);
    ly += 18;
  });
}

// RSSI distribution histogram
function drawRssiHistogram(canvas, networks, textClr, lineClr) {
  const { W, H, ctx } = _fitCanvas(canvas);
  const mg = { top: 16, right: 16, bottom: 36, left: 44 };
  const pW = W - mg.left - mg.right;
  const pH = H - mg.top  - mg.bottom;

  ctx.clearRect(0, 0, W, H);

  // Bins: -30 to -100, 10dBm each
  const bins = [-30,-40,-50,-60,-70,-80,-90,-100];
  const counts = new Array(bins.length).fill(0);
  networks.forEach(n => {
    const r = n.rssi ?? -100;
    const i = Math.min(counts.length - 1, Math.max(0, Math.floor((-30 - r) / 10)));
    counts[i]++;
  });

  const maxCount = Math.max(1, ...counts);
  const barW = pW / counts.length;

  // Grid
  ctx.strokeStyle = lineClr; ctx.lineWidth = 1;
  [0.25, 0.5, 0.75, 1].forEach(f => {
    const y = mg.top + pH - f * pH;
    ctx.beginPath(); ctx.moveTo(mg.left, y); ctx.lineTo(mg.left + pW, y); ctx.stroke();
    ctx.fillStyle = textClr; ctx.font = "10px ui-monospace,monospace"; ctx.textAlign = "right";
    ctx.fillText(Math.round(f * maxCount), mg.left - 6, y + 4);
  });

  counts.forEach((cnt, i) => {
    const norm  = cnt / maxCount;
    const x     = mg.left + i * barW + barW * 0.1;
    const bw    = barW * 0.8;
    const bh    = norm * pH;
    const y     = mg.top + pH - bh;

    // Color gradient: strong signal = green, weak = red
    const t     = 1 - i / (counts.length - 1);
    const r     = Math.round(248 * (1 - t) + 52  * t);
    const g     = Math.round(113 * (1 - t) + 211 * t);
    const b     = Math.round(113 * (1 - t) + 153 * t);
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(x, y, bw, bh);

    // Label
    ctx.fillStyle = textClr; ctx.font = "9px ui-monospace,monospace"; ctx.textAlign = "center";
    ctx.fillText(`${bins[i]}`, x + bw / 2, mg.top + pH + 16);
    if (cnt) ctx.fillText(String(cnt), x + bw / 2, y - 3);
  });

  // Axis label
  ctx.fillStyle = textClr + "88"; ctx.font = "9px ui-monospace,monospace"; ctx.textAlign = "center";
  ctx.fillText("dBm", mg.left + pW / 2, mg.top + pH + 30);
}

// Vendor horizontal bar chart
function drawVendorChart(canvas, vendors, textClr) {
  const { W, H, ctx } = _fitCanvas(canvas);
  if (!vendors.length) {
    ctx.fillStyle = textClr; ctx.font = "13px ui-monospace,monospace";
    ctx.textAlign = "center"; ctx.fillText("No vendor data", W / 2, H / 2);
    return;
  }
  const mg    = { top: 12, right: 24, bottom: 12, left: 140 };
  const pW    = W - mg.left - mg.right;
  const pH    = H - mg.top  - mg.bottom;
  const rows  = vendors.slice(0, 10);
  const barH  = Math.min(22, pH / rows.length - 4);
  const maxC  = Math.max(1, rows[0].count);

  ctx.clearRect(0, 0, W, H);

  rows.forEach((v, i) => {
    const y     = mg.top + i * (pH / rows.length);
    const bw    = (v.count / maxC) * pW;
    const color = CHART_COLORS[i % CHART_COLORS.length];

    ctx.fillStyle = color + "40";
    ctx.fillRect(mg.left, y + 2, bw, barH);
    ctx.fillStyle = color;
    ctx.fillRect(mg.left, y + 2, 3, barH);

    ctx.fillStyle = textClr; ctx.font = "10px ui-monospace,monospace";
    ctx.textAlign = "right";
    ctx.fillText((v.vendor || "Unknown").slice(0, 18), mg.left - 6, y + barH / 2 + 4);

    ctx.textAlign = "left";
    ctx.fillText(String(v.count), mg.left + bw + 5, y + barH / 2 + 4);
  });
}

// RSSI history modal (shown when clicking a network row)
function attachRssiClickHandlers() {
  document.querySelectorAll(".rm-rssi-row-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const bssid = btn.dataset.bssid;
      const ssid  = btn.dataset.ssid || bssid;
      await showRssiModal(bssid, ssid);
    });
  });
}

async function showRssiModal(bssid, ssid) {
  let modal = document.getElementById("rmRssiModal");
  if (modal) modal.remove();

  modal = document.createElement("div");
  modal.id        = "rmRssiModal";
  modal.className = "rm-rssi-modal";
  modal.innerHTML = `
    <div class="rm-rssi-modal-inner">
      <div class="rm-rssi-modal-header">
        <h3>RSSI History — ${esc(ssid)}</h3>
        <button class="rm-rssi-close" id="rmRssiCloseBtn">✕</button>
      </div>
      <canvas id="rmRssiHistCanvas" style="width:100%;height:200px;display:block"></canvas>
      <div class="rm-muted" id="rmRssiHistNote" style="font-size:0.72rem;margin-top:0.5rem;text-align:center">
        Loading…
      </div>
    </div>`;
  document.body.appendChild(modal);
  document.getElementById("rmRssiCloseBtn").addEventListener("click", () => modal.remove());
  modal.addEventListener("click", e => { if (e.target === modal) modal.remove(); });

  try {
    const history = await get(`/api/stats/rssi_history/${encodeURIComponent(bssid)}?minutes=60`);
    const note = document.getElementById("rmRssiHistNote");
    if (!history.length) { note.textContent = "No RSSI history yet — starts recording once scanning begins."; return; }
    note.textContent = `${history.length} samples over last 60 minutes`;
    drawRssiLine(document.getElementById("rmRssiHistCanvas"), history);
  } catch (e) {
    const note = document.getElementById("rmRssiHistNote");
    if (note) note.textContent = "Failed to load RSSI history.";
  }
}

function drawRssiLine(canvas, history) {
  const { W, H, ctx } = _fitCanvas(canvas);
  const mg = { top: 16, right: 16, bottom: 28, left: 44 };
  const pW = W - mg.left - mg.right;
  const pH = H - mg.top  - mg.bottom;

  ctx.clearRect(0, 0, W, H);

  const isDark = getTheme() === "dark";
  const textClr = isDark ? "#94a3b8" : "#475569";
  const lineClr = isDark ? "rgba(148,163,184,0.12)" : "rgba(15,23,42,0.08)";

  const vals  = history.map(p => p.rssi);
  const times = history.map(p => new Date(p.ts + "Z").getTime());
  const minT  = times[0], maxT = times[times.length - 1] || minT + 1;
  const minR  = Math.min(-90, ...vals), maxR = Math.max(-20, ...vals);

  function tx(t) { return mg.left + ((t - minT) / (maxT - minT || 1)) * pW; }
  function ry(r) { return mg.top + pH - ((r - minR) / (maxR - minR || 1)) * pH; }

  // Grid
  [-30,-50,-70,-90].forEach(r => {
    if (r < minR || r > maxR) return;
    ctx.strokeStyle = lineClr; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(mg.left, ry(r)); ctx.lineTo(mg.left + pW, ry(r)); ctx.stroke();
    ctx.fillStyle = textClr; ctx.font = "9px ui-monospace,monospace"; ctx.textAlign = "right";
    ctx.fillText(`${r}`, mg.left - 4, ry(r) + 3);
  });

  // Line
  ctx.beginPath();
  history.forEach((p, i) => {
    const x = tx(times[i]), y = ry(p.rssi);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#5ee1c8"; ctx.lineWidth = 2; ctx.stroke();

  // Fill under line
  ctx.lineTo(tx(times[times.length - 1]), mg.top + pH);
  ctx.lineTo(tx(times[0]),                mg.top + pH);
  ctx.closePath();
  ctx.fillStyle = "rgba(94,225,200,0.12)"; ctx.fill();

  // Time labels
  ctx.fillStyle = textClr; ctx.font = "9px ui-monospace,monospace"; ctx.textAlign = "center";
  [0, 0.5, 1].forEach(f => {
    const t = new Date(minT + f * (maxT - minT));
    const label = t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    ctx.fillText(label, mg.left + f * pW, mg.top + pH + 18);
  });
}

// ── AI view ───────────────────────────────────────────────────────────────────
const AI_HISTORY_KEY = "rm-ai-history";
const AI_HISTORY_MAX = 20;  // max turns to persist

function aiHistoryLoad() {
  try {
    const raw = localStorage.getItem(AI_HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}
function aiHistorySave(history) {
  try {
    // Keep last N turns to avoid localStorage bloat
    const trimmed = history.slice(-AI_HISTORY_MAX);
    localStorage.setItem(AI_HISTORY_KEY, JSON.stringify(trimmed));
  } catch { /* storage full — ignore */ }
}
function aiHistoryClear() {
  localStorage.removeItem(AI_HISTORY_KEY);
}

let aiHistory = aiHistoryLoad();

function viewAI(aiStatus) {
  const ready  = aiStatus.ready;
  const busy   = aiStatus.busy;
  const model  = aiStatus.model  || "not installed";
  const mb     = aiStatus.model_mb || 0;
  const binary = aiStatus.binary;

  const statusClass = ready ? "rm-ai-status-ok" : "rm-ai-status-off";
  const statusLabel = busy ? "busy" : ready ? "ready" : "not installed";

  const notInstalled = !binary || !aiStatus.model;
  const installNote = notInstalled ? `
    <div class="rm-ai-install-note">
      <strong>AI not installed.</strong> Run on the Pi:<br>
      <code>sudo bash setup/install_ai.sh</code><br>
      <span class="rm-muted" style="font-size:0.8rem">
        Downloads IBM Granite 1B (~400MB) and builds llama.cpp. Takes ~20 min on Pi Zero 2W.
      </span>
    </div>` : "";

  return `
    <div class="rm-ai-wrap">
      <div class="rm-ai-header">
        <div>
          <h2 style="margin:0;font-size:1.1rem;font-weight:700">AI Assistant</h2>
          <div class="rm-muted" style="font-size:0.8rem">IBM Granite 1B — runs locally on the Pi</div>
        </div>
        <div class="rm-ai-status-row">
          <span class="rm-ai-status-dot ${statusClass}"></span>
          <span class="rm-muted" style="font-size:0.8rem">${statusLabel}</span>
          ${ready ? `<span class="rm-mono rm-muted" style="font-size:0.75rem">${esc(model)} · ${mb}MB</span>` : ""}
        </div>
      </div>

      ${installNote}

      <div class="rm-ai-quick-btns">
        <button class="rm-btn rm-btn-primary rm-ai-analyze-btn" data-type="networks" ${ready && !busy ? "" : "disabled"}>
          Analyze Networks
        </button>
        <button class="rm-btn rm-btn-primary rm-ai-analyze-btn" data-type="passwords" ${ready && !busy ? "" : "disabled"}>
          Analyze Cracked Passwords
        </button>
        <button class="rm-btn rm-ai-clear-btn" title="Clear chat history">Clear</button>
      </div>

      <div class="rm-ai-chat" id="rmAiChat">
        ${aiHistory.length === 0
          ? `<div class="rm-ai-empty rm-muted">
               Ask anything about your scan data, or use the quick-analyze buttons above.
               <br><br>
               <span style="font-size:0.78rem">Note: inference takes 1–3 minutes on the Pi Zero 2W.</span>
             </div>`
          : aiHistory.map(m => renderAIBubble(m.role, m.content)).join("")
        }
      </div>

      <div class="rm-ai-input-row">
        <textarea class="rm-ai-input" id="rmAiInput" rows="2"
          placeholder="${ready ? "Ask about your networks, security risks, or passwords…" : "Install AI first to chat"}"
          ${ready && !busy ? "" : "disabled"}></textarea>
        <button class="rm-btn rm-btn-primary rm-ai-send" id="rmAiSendBtn"
          ${ready && !busy ? "" : "disabled"}>Send</button>
      </div>
      <div class="rm-ai-thinking" id="rmAiThinking" style="display:none">
        <span class="rm-spinner-sm"></span>
        <span class="rm-muted" style="font-size:0.82rem">Thinking… this may take a minute or two on the Pi.</span>
      </div>
    </div>`;
}

function renderAIBubble(role, content) {
  const cls = role === "assistant" ? "rm-ai-bubble-assistant" : "rm-ai-bubble-user";
  const label = role === "assistant" ? "Granite" : "You";
  return `<div class="rm-ai-bubble ${cls}">
    <div class="rm-ai-bubble-label">${esc(label)}</div>
    <div class="rm-ai-bubble-body">${esc(content)}</div>
  </div>`;
}

function renderAIThinking(show) {
  const el = document.getElementById("rmAiThinking");
  if (el) el.style.display = show ? "flex" : "none";
}

function setAIInputsDisabled(disabled) {
  document.querySelectorAll(".rm-ai-analyze-btn, #rmAiSendBtn, #rmAiInput").forEach(el => {
    el.disabled = disabled;
  });
}

function scrollAIChat() {
  const chat = document.getElementById("rmAiChat");
  if (chat) chat.scrollTop = chat.scrollHeight;
}

function appendAIBubble(role, content) {
  const chat = document.getElementById("rmAiChat");
  if (!chat) return;
  // Remove empty-state message
  const empty = chat.querySelector(".rm-ai-empty");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.innerHTML = renderAIBubble(role, content);
  chat.appendChild(div.firstElementChild);
  scrollAIChat();
}

async function aiSend(messages) {
  setAIInputsDisabled(true);
  renderAIThinking(true);
  try {
    const result = await post("/api/ai/chat", { messages });
    renderAIThinking(false);
    if (result.error) {
      appendAIBubble("assistant", `Error: ${result.error}`);
      aiHistory.push({ role: "assistant", content: `Error: ${result.error}` });
    } else {
      const resp = result.response || "(no response)";
      appendAIBubble("assistant", resp);
      aiHistory.push({ role: "assistant", content: resp });
    }
    aiHistorySave(aiHistory);
    if (result.elapsed) {
      const note = document.createElement("div");
      note.className = "rm-ai-elapsed rm-muted";
      note.textContent = `${result.elapsed}s`;
      document.getElementById("rmAiChat")?.appendChild(note);
    }
  } catch (e) {
    renderAIThinking(false);
    appendAIBubble("assistant", "Network error — is radioman running?");
  } finally {
    setAIInputsDisabled(false);
    scrollAIChat();
  }
}

function attachAIHandlers() {
  // Analyze buttons
  document.querySelectorAll(".rm-ai-analyze-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const type = btn.dataset.type;
      const label = type === "networks" ? "Analyze my scanned networks." : "Analyze my cracked passwords.";
      aiHistory = [{ role: "user", content: label }];
      appendAIBubble("user", label);

      setAIInputsDisabled(true);
      renderAIThinking(true);
      try {
        const result = await post("/api/ai/analyze", { type });
        renderAIThinking(false);
        const resp = result.response || result.error || "(no response)";
        appendAIBubble("assistant", resp);
        aiHistory.push({ role: "assistant", content: resp });
        aiHistorySave(aiHistory);
        if (result.elapsed) {
          const note = document.createElement("div");
          note.className = "rm-ai-elapsed rm-muted";
          note.textContent = `${result.elapsed}s`;
          document.getElementById("rmAiChat")?.appendChild(note);
        }
      } catch (e) {
        renderAIThinking(false);
        appendAIBubble("assistant", "Request failed.");
      } finally {
        setAIInputsDisabled(false);
        scrollAIChat();
      }
    });
  });

  // Clear button
  document.querySelector(".rm-ai-clear-btn")?.addEventListener("click", () => {
    aiHistory = [];
    aiHistoryClear();
    const chat = document.getElementById("rmAiChat");
    if (chat) chat.innerHTML = `<div class="rm-ai-empty rm-muted">
      Ask anything about your scan data, or use the quick-analyze buttons above.
    </div>`;
  });

  // Send on button click
  document.getElementById("rmAiSendBtn")?.addEventListener("click", () => sendUserMessage());

  // Send on Ctrl+Enter / Cmd+Enter
  document.getElementById("rmAiInput")?.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendUserMessage();
    }
  });

  scrollAIChat();
}

function sendUserMessage() {
  const input = document.getElementById("rmAiInput");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  aiHistory.push({ role: "user", content: text });
  aiHistorySave(aiHistory);
  appendAIBubble("user", text);
  aiSend([...aiHistory]);
}

// ── Kick off ──────────────────────────────────────────────────────────────────
poll();
pollTimer = setInterval(poll, POLL_MS);
