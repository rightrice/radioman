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
    const [status, data] = await Promise.all([
      get("/api/status"),
      fetchViewData(),
    ]);
    setStatus(true);
    document.getElementById("rmLoading")?.remove();
    updateUptime(status.personality?.uptime_seconds || 0);
    renderMain(status, data);
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
    default:          return null;
  }
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

function renderMain(status, data) {
  const main = document.getElementById("rmMain");
  switch (currentView) {
    case "overview":  main.innerHTML = viewOverview(status); break;
    case "networks":  main.innerHTML = viewNetworks(data || []); break;
    case "clients":   main.innerHTML = viewClients(data || []); break;
    case "captures":  main.innerHTML = viewCaptures(data || []); attachCrackHandlers(); break;
    case "graph":     main.innerHTML = viewGraph(); drawGraph(data || { nodes: [], edges: [] }); break;
    case "hosts":     main.innerHTML = viewHosts(data || [], status); attachScanHandler(); break;
    case "log":       main.innerHTML = viewLog(data || []); break;
  }
}

// ── Overview ──────────────────────────────────────────────────────────────────
function viewOverview(status) {
  const p    = status.personality || {};
  const b    = status.battery     || {};
  const s    = status.stats       || {};
  const cq   = status.crack_queue || {};

  const pct  = b.percent ?? -1;
  const chrg = b.charging ? " <span class='rm-charging'>⚡ charging</span>" : "";
  const hearts = makeHearts(pct);

  return `
    <div class="rm-overview-grid">
      <div class="rm-face-card">
        <div class="rm-mascot-wrap rm-mood-${esc(p.mood || "default")}">
          <img class="rm-mascot" src="/assets/img/radioman.png" alt="radioman" />
        </div>
        <div class="rm-mood-badge rm-mood-badge--${esc(p.mood || "default")}">${esc(p.mood || "default")}</div>
        <div class="rm-face-message">${esc(p.message || "")}</div>
        <div class="rm-battery">
          <span class="rm-hearts">${hearts}</span>
          <span class="rm-mono">${pct >= 0 ? pct + "%" : "—"}</span>
          ${chrg}
        </div>
      </div>
      <div class="rm-kpi-row">
        ${kpi("Networks", s.networks ?? 0, "teal")}
        ${kpi("Clients", s.clients ?? 0, "teal")}
        ${kpi("Handshakes", s.captures ?? 0, s.captures > 0 ? "ok" : "")}
        ${kpi("Cracked", s.cracked ?? 0, s.cracked > 0 ? "ok" : "")}
        ${kpi("Happiness", pct2bar(p.happiness ?? 0.5), "")}
        ${kpi("Queue", cq.queued ?? 0, cq.queued > 0 ? "warn" : "")}
      </div>
    </div>
    <div class="dash-panels">
      ${recentNetworksPanel()}
      ${recentEventsPanel()}
    </div>`;
}

function recentNetworksPanel() {
  return `
    <div class="dash-panel">
      <div class="dash-panel-header">
        <h3>Recent Networks</h3>
        <button class="rm-nav-btn" onclick="navigate('networks')">View all →</button>
      </div>
      <div class="dash-panel-body rm-empty">
        <div class="rm-empty-icon">📶</div>
        <p>Loading…</p>
      </div>
    </div>`;
}
function recentEventsPanel() {
  return `
    <div class="dash-panel">
      <div class="dash-panel-header">
        <h3>Recent Events</h3>
        <button class="rm-nav-btn" onclick="navigate('log')">View all →</button>
      </div>
      <div class="dash-panel-body rm-empty">
        <div class="rm-empty-icon">📋</div>
        <p>Loading…</p>
      </div>
    </div>`;
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
            <th>Signal</th><th>Security</th><th>Clients</th><th>Last Seen</th>
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
            <th>Captured</th><th>Status</th><th>Action</th>
          </tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td class="rm-table-ssid">${esc(r.ssid || "—")}</td>
                <td class="rm-mono rm-table-bssid">${esc(r.bssid || "—")}</td>
                <td>${esc(r.type || "—")}</td>
                <td class="rm-muted">${shortDate(r.captured_at)}</td>
                <td>${r.cracked
                  ? `<span class="rm-crack-badge-ok">✓ ${esc(r.password || "found")}</span>`
                  : `<span class="rm-muted">pending</span>`}</td>
                <td>${!r.cracked
                  ? `<button class="rm-crack-btn" data-id="${r.id}">Crack</button>`
                  : ""}</td>
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
function viewHosts(rows, status) {
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

// ── Kick off ──────────────────────────────────────────────────────────────────
poll();
pollTimer = setInterval(poll, POLL_MS);
