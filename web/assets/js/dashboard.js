/* radioman dashboard — vanilla JS, no framework dependencies */

const API = "";
const POLL_MS = 5000;

let currentView = "overview";
let pollTimer   = null;
let graphNodes  = [];
let graphEdges  = [];
let graphAnim   = null;
let graphPositions = {};   // id -> {x,y,vx,vy}, persists across polls
let graphSelected  = null; // id of clicked node
let graphSettle    = 0;    // frames the layout has been "cool"
let graphHeat      = {};   // ap id -> 0..1 congestion (drives node colour)
let graphMode      = "wifi"; // "wifi" (APs↔clients) or "lan" (gateway↔hosts)
let graphCtx = null, graphW = 0, graphH = 0, graphDpr = 1;
let MY_BSSID    = "";   // user's own network, set from /api/status
let CAPS        = {};   // hardware capabilities from /api/status (wifi_monitor, rogue_ap, gps)
let rmMap = null, rmMapMarkers = null, rmMapTrack = null, rmMapPos = null;
let rmMapFitted = false;
let activeTab   = "targets";  // Active-view sub-tab (persists across polls)
let activeCache = null;       // last Active payload, for instant sub-tab switching
let accState    = {};         // <details> open-state, preserved across re-renders
let lastActiveSig = null;     // skip Active re-render when the visible data is unchanged

// ── Theme ─────────────────────────────────────────────────────────────────────
const root = document.getElementById("rmRoot");
const settingsBtn = document.getElementById("rmSettingsBtn");

function getTheme() {
  return localStorage.getItem("rm-theme") || "dark";
}
function setTheme(t) {
  root.setAttribute("data-aap-theme", t);
  document.documentElement.setAttribute("data-aap-theme", t);
  localStorage.setItem("rm-theme", t);
  // Update the in-page theme toggle if the Settings view is mounted.
  const tog = document.getElementById("rmThemeToggle");
  if (tog) tog.textContent = t === "dark" ? "Dark ☾" : "Light ☀︎";
}
setTheme(getTheme());
settingsBtn.addEventListener("click", () => navigate("settings"));

// ── Navigation (grouped: 7 top-level groups, sub-nav for multi-view groups) ────
const NAV_GROUPS = [
  { id: "overview",  views: [["overview", "Overview"]] },
  { id: "discovery", views: [["networks", "Networks"], ["clients", "Clients"], ["ble", "Bluetooth"], ["hosts", "LAN Hosts"]] },
  { id: "visualize", views: [["graph", "Graph"], ["map", "Map"], ["stats", "Stats"]] },
  { id: "captures",  views: [["captures", "Captures"]] },
  { id: "active",    views: [["active", "Active"]] },
  { id: "ai",        views: [["ai", "AI"]] },
  { id: "system",    views: [["log", "Log"], ["ignore", "Ignore"], ["settings", "Settings"]] },
];
let currentGroup = "overview";
const groupLastView = {};   // groupId -> last sub-view used, so a group reopens where you left it

function groupForView(view) {
  return NAV_GROUPS.find(g => g.views.some(v => v[0] === view)) || NAV_GROUPS[0];
}
function highlightNav() {
  document.querySelectorAll("#rmNav .rm-nav-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.group === currentGroup));
}
function renderGroupNav() {
  const host = document.getElementById("rmGroupNav");
  if (!host) return;
  const g = NAV_GROUPS.find(x => x.id === currentGroup) || NAV_GROUPS[0];
  if (g.views.length <= 1) { host.innerHTML = ""; host.classList.remove("rm-groupnav-on"); return; }
  host.classList.add("rm-groupnav-on");
  host.innerHTML = g.views.map(([v, label]) =>
    `<button class="rm-groupnav-btn ${v === currentView ? "active" : ""}" data-view="${v}" role="tab">${label}</button>`
  ).join("");
}
function selectGroup(groupId) {
  const g = NAV_GROUPS.find(x => x.id === groupId) || NAV_GROUPS[0];
  currentGroup = g.id;
  const remembered = groupLastView[g.id];
  currentView = (remembered && g.views.some(v => v[0] === remembered)) ? remembered : g.views[0][0];
  groupLastView[g.id] = currentView;
  highlightNav();
  renderGroupNav();
  renderView();
}

document.getElementById("rmNav").addEventListener("click", e => {
  const btn = e.target.closest(".rm-nav-btn");
  if (btn) selectGroup(btn.dataset.group);
});
document.getElementById("rmGroupNav").addEventListener("click", e => {
  const btn = e.target.closest(".rm-groupnav-btn");
  if (!btn) return;
  currentView = btn.dataset.view;
  groupLastView[currentGroup] = currentView;
  renderGroupNav();
  renderView();
});
renderGroupNav();   // initial paint (overview group has one view → hidden)

// ── Fetch helpers ─────────────────────────────────────────────────────────────
async function get(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}
async function post(path, body = {}, timeoutMs = 0) {
  const opts = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  let timer;
  if (timeoutMs > 0) {
    const ctrl = new AbortController();
    opts.signal = ctrl.signal;
    timer = setTimeout(() => ctrl.abort(), timeoutMs);
  }
  try {
    const r = await fetch(API + path, opts);
    return r.json();
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// AI inference can take minutes on the Pi Zero 2W — keep the client just above
// the daemon's 300s inference timeout so the daemon's error wins the race.
const AI_TIMEOUT_MS = 315000;

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
    case "ble":       return get("/api/bluetooth");
    case "captures":  return Promise.all([get("/api/captures"), get("/api/passwords"), get("/api/vault")]);
    case "graph":     return graphMode === "lan" ? get("/api/hosts")
                           : graphMode === "l3"  ? get("/api/topology")
                           : get("/api/graph");
    case "map":       return get("/api/wardrive");
    case "hosts":     return Promise.all([get("/api/hosts"), get("/api/hosts/scanstatus")]);
    case "log":       return get("/api/events?limit=100");
    case "ignore":    return get("/api/ignore");
    case "stats":     return Promise.all([get("/api/networks"), get("/api/stats")]);
    case "ai":        return get("/api/ai/status");
    case "active":    return Promise.all([get("/api/offensive/status"), get("/api/scope"), get("/api/audit"), get("/api/networks"), get("/api/rogueap/status"), get("/api/rogueap/loot"), get("/api/lab"), get("/api/scope/engagements")]);
    case "settings":  return Promise.all([get("/api/settings"), get("/api/wifi/status"), get("/api/networks")]);
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

  // Don't let the 5s poll clobber a form the user is typing into. Keep the
  // Active payload cached (so a manual sub-tab switch is still fresh), then bail.
  const ae = document.activeElement;
  if (ae && ae !== document.body && main.contains(ae) &&
      /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName)) {
    if (currentView === "active" && Array.isArray(data)) {
      const a = data;
      activeCache = [a[0] || {}, a[1] || [], a[2] || [], a[3] || [], a[4] || {}, a[5] || {}, a[6] || [], a[7] || []];
    }
    return;
  }

  MY_BSSID = (status?.my_bssid || "").toUpperCase();
  CAPS = status?.capabilities || {};
  // Stop the graph animation loop when not viewing it.
  if (currentView !== "graph" && graphAnim) {
    cancelAnimationFrame(graphAnim); graphAnim = null;
  }
  switch (currentView) {
    case "overview": {
      const [recentNets, recentEvents] = Array.isArray(data) ? data : [[], []];
      main.innerHTML = viewOverview(status, xplt, recentNets || [], recentEvents || []);
      attachXpltHandler(); attachScanToggle();
      break;
    }
    case "networks":  main.innerHTML = viewNetworks(data || []); attachIgnoreHandlers(); attachDeleteHandlers(); break;
    case "clients":   main.innerHTML = viewClients(data || []); attachDeleteHandlers(); break;
    case "ble":       main.innerHTML = viewBluetooth(data || []); attachDeleteHandlers(); break;
    case "captures": {
      const [caps, pw, vault] = Array.isArray(data) ? data : [[], {}, {}];
      main.innerHTML = viewCaptures(caps || [], pw || {}, vault || {});
      attachCrackHandlers();
      attachVaultHandlers();
      break;
    }
    case "graph": {
      // Build the canvas once; subsequent polls only feed new data (no flash,
      // selection + layout persist).
      if (!document.getElementById("rmGraphCanvas")) main.innerHTML = viewGraph();
      attachGraphControls();
      const gdata = graphMode === "lan" ? hostsToGraph(data || [])
                  : (data || { nodes: [], edges: [] });   // wifi + l3 are already {nodes,edges}
      drawGraph(gdata);
      syncTopoUI(graphMode === "l3" ? data : null);
      break;
    }
    case "map": {
      // Build the container once; the Leaflet instance persists across polls so
      // the user's pan/zoom isn't reset every 5s. Navigating away wipes the div,
      // so rebuild + re-init on return.
      if (!document.getElementById("rmMapCanvas")) {
        if (rmMap) { try { rmMap.remove(); } catch (e) {} rmMap = null; }
        main.innerHTML = viewMap();
        rmMapFitted = false;
      }
      attachMapHandlers();
      drawMap(data || {});
      break;
    }
    case "hosts": {
      const [hostRows, scan] = Array.isArray(data) ? data : [[], {}];
      main.innerHTML = viewHosts(hostRows || [], scan || {});
      attachScanHandler();
      break;
    }
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
    case "active": {
      const a = Array.isArray(data) ? data : [{}, [], [], [], {}, {}, [], []];
      activeCache = [a[0] || {}, a[1] || [], a[2] || [], a[3] || [], a[4] || {}, a[5] || {}, a[6] || [], a[7] || []];
      const sig = activeSig();
      if (sig !== lastActiveSig || !main.querySelector(".rm-subnav")) {
        lastActiveSig = sig;
        main.innerHTML = viewActive(...activeCache);
        attachActiveHandlers();
      }
      break;
    }
    case "settings": {
      const [settings, wifi, nets] = Array.isArray(data) ? data : [{}, {}, []];
      main.innerHTML = viewSettings(settings || {}, wifi || {}, nets || []);
      attachSettingsHandlers();
      break;
    }
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
      ${kpi("Bluetooth", s.bluetooth ?? 0, s.bluetooth > 0 ? "teal" : "")}
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
const DEVICE_ICONS = {
  router: "📡", phone: "📱", computer: "💻", iot: "💡", tv: "📺",
  printer: "🖨", camera: "📷", voip: "☎️", wearable: "⌚",
  gaming: "🎮", sbc: "📟", audio: "🎧",
};
function deviceTag(type) {
  const ic = DEVICE_ICONS[type];
  return ic ? `<span class="rm-dev" title="${esc(type)}">${ic}</span> ` : "";
}

function viewNetworks(rows) {
  if (!rows.length) return empty("📶", "No networks discovered yet");
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} network${rows.length !== 1 ? "s" : ""} discovered</span>
      <span class="rm-purge-group">
        <label class="rm-muted" for="rmPurgeDays">Purge older than</label>
        <select id="rmPurgeDays" class="rm-purge-select">
          <option value="1">1 day</option>
          <option value="3">3 days</option>
          <option value="7" selected>7 days</option>
          <option value="15">15 days</option>
        </select>
        <button class="rm-purge-btn" id="rmPurgeNetworks" title="Delete networks not seen since the chosen age">Purge</button>
      </span>
    </div>
    <div class="dash-panel dash-panel-full">
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr>
            <th>SSID</th><th>BSSID</th><th>CH</th>
            <th>Signal</th><th>Security</th><th>Clients</th><th>Last Seen</th><th></th>
          </tr></thead>
          <tbody>
            ${rows.map(r => {
              const mine = MY_BSSID && (r.bssid || "").toUpperCase() === MY_BSSID;
              return `
              <tr class="${mine ? "rm-row-mine" : ""}">
                <td class="rm-table-ssid">${mine ? '<span class="rm-mine-star" title="Your network">★</span> ' : ""}${deviceTag(r.device_type)}${esc(r.ssid || "—")}</td>
                <td class="rm-table-bssid rm-mono">${esc(r.bssid)}</td>
                <td>${r.channel ?? "—"}</td>
                <td>${rssiCell(r.rssi)}</td>
                <td>${secBadge(r.security)}</td>
                <td>${r.clients ?? 0}</td>
                <td class="rm-muted">${shortDate(r.last_seen)}</td>
                <td class="rm-row-actions">
                  <button class="rm-ignore-btn" data-bssid="${esc(r.bssid)}" title="Add to ignore list">Ignore</button>
                  <button class="rm-delete-btn rm-delete-network" data-bssid="${esc(r.bssid)}" title="Delete this network record">Delete</button>
                </td>
              </tr>`; }).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ── Clients ───────────────────────────────────────────────────────────────────
function viewClients(rows) {
  const gate = CAPS.wifi_monitor ? "" : hwGate(GATE_MONITOR);
  if (!rows.length) return gate + empty("📱", CAPS.wifi_monitor ? "No clients discovered yet" : "No clients — monitor mode required");
  return `
    ${gate}
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} client${rows.length !== 1 ? "s" : ""} seen</span>
    </div>
    <div class="dash-panel dash-panel-full">
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr>
            <th>MAC</th><th>Associated AP</th><th>Vendor</th><th>Signal</th><th>Last Seen</th><th></th>
          </tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td class="rm-mono">${deviceTag(r.device_type)}${esc(r.mac)}</td>
                <td class="rm-mono rm-muted">${esc(r.bssid || "—")}</td>
                <td>${esc(r.vendor || "—")}</td>
                <td>${rssiCell(r.rssi)}</td>
                <td class="rm-muted">${shortDate(r.last_seen)}</td>
                <td><button class="rm-delete-btn rm-delete-client" data-mac="${esc(r.mac)}" title="Delete this client record">Delete</button></td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ── Bluetooth ─────────────────────────────────────────────────────────────────
function viewBluetooth(rows) {
  if (!rows.length) return empty("🔵", "No Bluetooth devices seen yet");
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} Bluetooth device${rows.length !== 1 ? "s" : ""} seen</span>
      <span class="rm-purge-group">
        <label class="rm-muted" for="rmPurgeBleDays">Purge older than</label>
        <select id="rmPurgeBleDays" class="rm-purge-select">
          <option value="1">1 day</option>
          <option value="3">3 days</option>
          <option value="7" selected>7 days</option>
          <option value="15">15 days</option>
        </select>
        <button class="rm-purge-btn" id="rmPurgeBle" title="Delete BT devices not seen since the chosen age">Purge</button>
      </span>
    </div>
    <div class="dash-panel dash-panel-full">
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr>
            <th>Name</th><th>MAC</th><th>Vendor</th><th>Signal</th><th>Last Seen</th><th></th>
          </tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td>${deviceTag(r.device_type)}${esc(r.name || "—")}</td>
                <td class="rm-mono rm-muted">${esc(r.mac)}</td>
                <td>${esc(r.vendor || "—")}</td>
                <td>${rssiCell(r.rssi)}</td>
                <td class="rm-muted">${shortDate(r.last_seen)}</td>
                <td><button class="rm-delete-btn rm-delete-ble" data-mac="${esc(r.mac)}" title="Delete this device record">Delete</button></td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ── Map / Wardrive ────────────────────────────────────────────────────────────
function viewMap() {
  return `
    <div class="rm-action-bar">
      <span class="rm-muted" id="rmMapSummary">Wardrive map</span>
      <span class="rm-gps-badge" id="rmGpsBadge">GPS: …</span>
      <button class="rm-purge-btn" id="rmClearTrack" title="Delete the recorded breadcrumb track" style="margin-left:auto">Clear track</button>
    </div>
    <div class="dash-panel dash-panel-full">
      <div id="rmMapCanvas" class="rm-map"></div>
    </div>`;
}

// Colour APs on the map by security posture (open = danger, WPA3 = safe).
function secColor(sec) {
  const s = (sec || "").toUpperCase();
  if (s.includes("WPA3")) return "#22c55e";
  if (s.includes("WPA2")) return "#3b82f6";
  if (s.includes("WPA"))  return "#eab308";
  if (s.includes("WEP"))  return "#f97316";
  if (s.includes("OPEN") || s === "" || s === "NONE") return "#ef4444";
  return "#94a3b8";
}

function drawMap(data) {
  const nets  = data.networks || [];
  const track = data.track || [];
  const fix   = data.fix || {};
  const enabled = !!data.enabled;

  const badge = document.getElementById("rmGpsBadge");
  if (badge) {
    if (!enabled)            badge.textContent = "GPS: disabled (set [gps] mode in config)";
    else if (fix && fix.fix) badge.textContent = `GPS: ${fix.lat.toFixed(5)}, ${fix.lon.toFixed(5)} ±${Math.round(fix.accuracy || 0)}m`;
    else                     badge.textContent = "GPS: no fix";
    badge.classList.toggle("rm-gps-on", !!(fix && fix.fix));
  }
  const summ = document.getElementById("rmMapSummary");
  if (summ) summ.textContent = `${nets.length} geolocated network${nets.length !== 1 ? "s" : ""} · ${track.length} track point${track.length !== 1 ? "s" : ""}`;

  const canvas = document.getElementById("rmMapCanvas");
  if (!canvas) return;
  if (typeof L === "undefined") {
    canvas.innerHTML = `<div class="rm-map-fallback">Map library couldn't load — the dashboard host needs internet access to fetch Leaflet &amp; map tiles.</div>`;
    return;
  }

  if (!rmMap) {
    rmMap = L.map(canvas, { attributionControl: true }).setView([0, 0], 2);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap",
    }).addTo(rmMap);
    rmMapTrack   = L.polyline([], { color: "#d9622a", weight: 3, opacity: 0.7 }).addTo(rmMap);
    rmMapMarkers = L.layerGroup().addTo(rmMap);
    rmMapFitted  = false;
  }

  const bounds = [];

  rmMapMarkers.clearLayers();
  nets.forEach(n => {
    if (n.lat == null || n.lon == null) return;
    bounds.push([n.lat, n.lon]);
    const c = secColor(n.security);
    const m = L.circleMarker([n.lat, n.lon], {
      radius: 6, color: c, fillColor: c, fillOpacity: 0.7, weight: 1,
    });
    m.bindPopup(`<b>${esc(n.ssid || "—")}</b><br>${esc(n.bssid)}<br>${esc(n.security || "?")} · ${n.rssi}dBm${n.gps_accuracy ? ` · ±${Math.round(n.gps_accuracy)}m` : ""}`);
    rmMapMarkers.addLayer(m);
  });

  const tpts = track.filter(t => t.lat != null && t.lon != null).map(t => [t.lat, t.lon]);
  rmMapTrack.setLatLngs(tpts);
  tpts.forEach(p => bounds.push(p));

  if (fix && fix.fix && fix.lat != null) {
    const here = [fix.lat, fix.lon];
    bounds.push(here);
    const cm = L.circleMarker(here, {
      radius: 8, color: "#22d3ee", fillColor: "#22d3ee", fillOpacity: 0.9, weight: 2,
    }).bindPopup("Current position");
    rmMapMarkers.addLayer(cm);
  }

  // Fit to data once, so subsequent polls don't yank the view while panning.
  if (!rmMapFitted && bounds.length) {
    rmMap.fitBounds(bounds, { padding: [30, 30], maxZoom: 17 });
    rmMapFitted = true;
  }
  // The container was display:none until now in some flows — recompute size.
  setTimeout(() => { if (rmMap) rmMap.invalidateSize(); }, 0);
}

function attachMapHandlers() {
  const clr = document.getElementById("rmClearTrack");
  if (clr && !clr._wired) {
    clr._wired = true;
    clr.addEventListener("click", async () => {
      if (!confirm("Delete the recorded wardrive track?\nGeolocated networks are kept.")) return;
      clr.textContent = "Clearing…";
      clr.disabled = true;
      try {
        await del("/api/wardrive/track");
        rmMapFitted = false;
        poll();
      } catch (e) { clr.textContent = "Error"; }
      clr.textContent = "Clear track";
      clr.disabled = false;
    });
  }
}

// ── Captures ──────────────────────────────────────────────────────────────────
function passwordIntelPanel(a) {
  if (!a || !a.total) return "";
  const ratingColor = { "very weak": "var(--rm-red)", "weak": "var(--rm-red)",
    "fair": "var(--rm-amber)", "strong": "var(--rm-green)", "very strong": "var(--rm-green)" };
  const ratings = Object.entries(a.ratings || {})
    .map(([k, v]) => `<span class="rm-pw-pill" style="color:${ratingColor[k] || "var(--aap-muted)"}">${esc(k)}: ${v}</span>`).join("");
  const pats = Object.entries(a.patterns || {}).slice(0, 8)
    .map(([k, v]) => `<span class="rm-pw-pill">${esc(k)} <span class="rm-muted">×${v}</span></span>`).join("") || "<span class='rm-muted'>none</span>";
  const reuse = (a.reuse || []).length
    ? a.reuse.map(r => `<div class="rm-muted" style="font-size:0.78rem">${esc(r.masked)} — reused on ${r.count}: ${esc(r.ssids.join(", "))}</div>`).join("")
    : "<span class='rm-muted'>none</span>";
  const recs = (a.recommendations || []).map(r => `<li>${esc(r)}</li>`).join("");
  return `
    <div class="dash-panel dash-panel-full" style="margin-bottom:1rem">
      <div class="dash-panel-header"><h3>Password intelligence</h3>
        <span class="rm-muted">${a.total} cracked · avg ${a.avg_entropy} bits · ${a.weak_pct}% weak</span></div>
      <div class="rm-pw-body">
        <div class="rm-pw-row"><span class="rm-pw-label">Strength</span><div>${ratings}</div></div>
        <div class="rm-pw-row"><span class="rm-pw-label">Patterns</span><div>${pats}</div></div>
        <div class="rm-pw-row"><span class="rm-pw-label">Factory-default shapes</span><div>${a.defaults}</div></div>
        <div class="rm-pw-row"><span class="rm-pw-label">Reused keys</span><div>${reuse}</div></div>
        ${recs ? `<div class="rm-pw-row"><span class="rm-pw-label">Recommendations</span><ul class="rm-pw-recs">${recs}</ul></div>` : ""}
      </div>
    </div>`;
}

function vaultBanner(v) {
  if (!v || !v.enabled) return "";
  const fp = v.fingerprint ? `<span class="rm-mono">${esc(v.fingerprint)}</span>` : "—";
  const counts = `${v.encrypted || 0} encrypted${v.plaintext ? ` · ${v.plaintext} plaintext` : ""}`;
  if (v.locked) {
    return `
      <div class="rm-roe-banner" style="margin-bottom:1rem">
        <span class="rm-roe-icon">🔒</span>
        <div style="flex:1">
          <strong>Capture vault is LOCKED</strong> (mode: ${esc(v.mode)}). Encrypted captures can't be cracked or downloaded until you unlock. ${counts}.
          <div class="rm-ignore-form" style="padding:0.6rem 0 0">
            <input class="rm-ignore-input" id="rmVaultPin" type="password" placeholder="Passphrase / PIN" maxlength="128" style="max-width:280px">
            <button class="rm-btn rm-btn-primary" id="rmVaultUnlock">Unlock</button>
          </div>
        </div>
      </div>`;
  }
  return `
    <div class="rm-roe-banner rm-roe-armed" style="margin-bottom:1rem">
      <span class="rm-roe-icon">🔓</span>
      <div style="flex:1">
        <strong>Capture vault unlocked</strong> (mode: ${esc(v.mode)}) — key ${fp} · ${counts}. New captures are encrypted at rest; cracking & downloads decrypt transparently.
      </div>
      ${v.mode === "pin" ? `<button class="rm-btn" id="rmVaultLock">Lock</button>` : ""}
    </div>`;
}

function viewCaptures(rows, pwAnalysis, vault) {
  const gate = CAPS.wifi_monitor ? "" : hwGate(GATE_MONITOR);
  if (!rows.length) return gate + (vaultBanner(vault) || "") + empty("🔐", "No handshakes captured yet");
  return `
    ${gate}
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} capture${rows.length !== 1 ? "s" : ""}</span>
    </div>
    ${vaultBanner(vault)}
    ${passwordIntelPanel(pwAnalysis)}
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
                <td><span class="rm-cap-type">${esc(r.type || "—")}</span>${r.encrypted ? ` <span class="rm-enc-badge" title="Encrypted at rest">🔒</span>` : ""}</td>
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

function attachVaultHandlers() {
  const unlock = document.getElementById("rmVaultUnlock");
  if (unlock) unlock.addEventListener("click", async () => {
    const pin = (document.getElementById("rmVaultPin")?.value || "");
    if (!pin) { document.getElementById("rmVaultPin")?.focus(); return; }
    unlock.textContent = "Unlocking…"; unlock.disabled = true;
    try {
      const r = await post("/api/vault/unlock", { passphrase: pin });
      if (r.error) { alert(r.error); unlock.textContent = "Unlock"; unlock.disabled = false; }
      else poll();
    } catch (e) { unlock.textContent = "Unlock"; unlock.disabled = false; }
  });
  document.getElementById("rmVaultPin")?.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); unlock?.click(); }
  });

  const lock = document.getElementById("rmVaultLock");
  if (lock) lock.addEventListener("click", async () => {
    lock.disabled = true;
    try { await post("/api/vault/lock", {}); poll(); } catch (e) { lock.disabled = false; }
  });
}

// ── Graph ─────────────────────────────────────────────────────────────────────
function graphLegendHtml() {
  if (graphMode === "lan")
    return `<span class="rm-legend-gateway">Gateway / Router</span><span class="rm-legend-host">Hosts</span>`;
  if (graphMode === "l3")
    return `<span class="rm-legend-gateway">Gateway / L3</span><span class="rm-legend-router">Routers</span><span class="rm-legend-subnet">Subnets / VLANs</span><span class="rm-legend-host">Hosts</span>`;
  return `<span class="rm-legend-ap">APs — low → high congestion</span><span class="rm-legend-client">Clients</span>`;
}

function viewGraph() {
  return `
    <div class="dash-panel dash-panel-full">
      <div class="dash-panel-header">
        <h3>Network Graph</h3>
        <div class="rm-graph-controls">
          <select class="rm-graph-mode" id="rmGraphMode" aria-label="Graph view">
            <option value="wifi" ${graphMode === "wifi" ? "selected" : ""}>WiFi Topology — APs &amp; clients</option>
            <option value="lan"  ${graphMode === "lan"  ? "selected" : ""}>LAN Topology — hosts</option>
            <option value="l3"   ${graphMode === "l3"   ? "selected" : ""}>L3 Topology — routers, subnets, VLANs</option>
          </select>
          <button class="rm-btn rm-btn-primary rm-graph-discover" id="rmTopoBtn" style="display:none">Discover L3 / VLANs</button>
          <span class="rm-muted rm-topo-note" id="rmTopoNote" style="font-size:0.72rem">click a node for details</span>
        </div>
      </div>
      <div class="rm-graph-wrap">
        <canvas id="rmGraphCanvas"></canvas>
        <div class="rm-graph-legend" id="rmGraphLegend">${graphLegendHtml()}</div>
        <div class="rm-graph-info" id="rmGraphInfo" style="display:none"></div>
      </div>
    </div>`;
}

function hostsToGraph(hosts) {
  hosts = hosts || [];
  const gw = hosts.find(h => (h.ip || "").endsWith(".1"));
  const gwId = gw ? gw.ip : "__gateway__";
  const nodes = [gw
    ? { id: gw.ip, label: gw.hostname || gw.vendor || gw.ip, type: "gateway",
        ip: gw.ip, mac: gw.mac, vendor: gw.vendor, hostname: gw.hostname }
    : { id: gwId, label: "Gateway", type: "gateway", ip: "", mac: "", vendor: "", hostname: "" }];
  const edges = [];
  hosts.forEach(h => {
    if (gw && h.ip === gw.ip) return;
    const id = h.ip || h.mac;
    if (!id) return;
    nodes.push({ id, label: h.hostname || h.vendor || h.ip, type: "host",
                 ip: h.ip, mac: h.mac, vendor: h.vendor, hostname: h.hostname });
    edges.push({ source: gwId, target: id });
  });
  return { nodes, edges };
}

function attachGraphControls() {
  const sel = document.getElementById("rmGraphMode");
  if (sel && !sel._rmBound) {
    sel._rmBound = true;
    sel.addEventListener("change", () => {
      graphMode = sel.value;
      graphPositions = {}; graphSelected = null; updateGraphInfo(null);
      const legend = document.getElementById("rmGraphLegend");
      if (legend) legend.innerHTML = graphLegendHtml();
      syncTopoUI(null);
      poll();   // re-fetch with the new data source
    });
  }
  const btn = document.getElementById("rmTopoBtn");
  if (btn && !btn._rmBound) {
    btn._rmBound = true;
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "Discovering… ⟳";
      try { await post("/api/topology/scan"); setTimeout(poll, 800); }
      catch (e) { btn.disabled = false; btn.textContent = "Discover L3 / VLANs"; }
    });
  }
}

function syncTopoUI(topoData) {
  const btn  = document.getElementById("rmTopoBtn");
  const note = document.getElementById("rmTopoNote");
  if (!btn) return;
  if (graphMode !== "l3") {
    btn.style.display = "none";
    if (note) note.textContent = "click a node for details";
    return;
  }
  btn.style.display = "";
  const scan = (topoData && topoData.scan) || {};
  const meta = (topoData && topoData.meta) || {};
  btn.disabled = !!scan.scanning;
  btn.textContent = scan.scanning ? "Discovering… ⟳" : "Discover L3 / VLANs";
  if (note) {
    const bits = [];
    if (scan.last_error)       bits.push(scan.last_error);
    else if (meta.note)        bits.push(meta.note);
    if (meta.vlans && meta.vlans.length)     bits.push(`${meta.vlans.length} VLANs`);
    if (meta.subnets && meta.subnets.length) bits.push(`${meta.subnets.length} subnets`);
    note.textContent = bits.join(" · ") || "click a node for details";
  }
}

function drawGraph(data) {
  graphNodes = data.nodes || [];
  graphEdges = data.edges || [];

  const canvas = document.getElementById("rmGraphCanvas");
  if (!canvas) {
    if (graphAnim) { cancelAnimationFrame(graphAnim); graphAnim = null; }
    return;
  }

  graphDpr = window.devicePixelRatio || 1;
  graphW = canvas.offsetWidth  || 800;
  graphH = canvas.offsetHeight || 480;
  const needW = Math.round(graphW * graphDpr), needH = Math.round(graphH * graphDpr);
  if (canvas.width !== needW || canvas.height !== needH) {  // avoid clearing/flicker on stable size
    canvas.width = needW; canvas.height = needH;
  }
  graphCtx = canvas.getContext("2d");

  // Keep existing node positions across polls (so the layout settles instead of
  // restarting); seed new nodes near the centre, drop nodes that disappeared.
  const ids = new Set(graphNodes.map(n => n.id));
  Object.keys(graphPositions).forEach(id => { if (!ids.has(id)) delete graphPositions[id]; });
  graphNodes.forEach((n, i) => {
    if (!graphPositions[n.id]) {
      const a = (i / Math.max(1, graphNodes.length)) * Math.PI * 2;
      graphPositions[n.id] = {
        x: graphW / 2 + Math.cos(a) * 60 + (Math.random() - 0.5) * 30,
        y: graphH / 2 + Math.sin(a) * 60 + (Math.random() - 0.5) * 30,
        vx: 0, vy: 0,
      };
    }
  });
  if (graphSelected && !ids.has(graphSelected)) { graphSelected = null; updateGraphInfo(null); }

  // Heat-colour AP nodes by congestion contribution (band-agnostic co-channel).
  graphHeat = {};
  const aps = graphNodes.filter(n => n.type === "ap");
  const congAt = ch => aps.reduce(
    (s, n) => s + sigWeight(n.rssi) * Math.max(0, 1 - Math.abs(ch - n.channel) / 5), 0);
  const scores = aps.map(n => sigWeight(n.rssi) * congAt(n.channel));
  const maxScore = Math.max(1, ...scores);
  aps.forEach((n, i) => { graphHeat[n.id] = scores[i] / maxScore; });

  if (!canvas._rmClickBound) {
    canvas.addEventListener("click", onGraphClick);
    canvas._rmClickBound = true;
  }

  graphSettle = 0;                       // re-energise briefly on refresh
  if (!graphAnim) graphAnim = requestAnimationFrame(graphStep);
}

function graphStep() {
  graphTick();
  graphRender();
  if (graphSettle > 60) { graphAnim = null; return; }  // stop when cool (saves CPU)
  graphAnim = requestAnimationFrame(graphStep);
}

function graphTick() {
  const W = graphW, H = graphH, P = graphPositions;
  let energy = 0;

  // Repulsion (normalised direction × inverse-square magnitude)
  graphNodes.forEach(a => {
    const pa = P[a.id]; if (!pa) return;
    graphNodes.forEach(b => {
      if (a.id === b.id) return;
      const pb = P[b.id]; if (!pb) return;
      const dx = pa.x - pb.x, dy = pa.y - pb.y;
      const d  = Math.sqrt(dx * dx + dy * dy) || 1;
      const f  = 1400 / (d * d);
      pa.vx += (dx / d) * f; pa.vy += (dy / d) * f;
    });
  });

  // Edge springs
  graphEdges.forEach(e => {
    const pa = P[e.source], pb = P[e.target];
    if (!pa || !pb) return;
    const dx = pb.x - pa.x, dy = pb.y - pa.y;
    const d  = Math.sqrt(dx * dx + dy * dy) || 1;
    const f  = (d - 70) * 0.02;
    pa.vx += (dx / d) * f; pa.vy += (dy / d) * f;
    pb.vx -= (dx / d) * f; pb.vy -= (dy / d) * f;
  });

  // Gravity toward centre — keeps nodes from drifting to the edges
  graphNodes.forEach(n => {
    const p = P[n.id]; if (!p) return;
    p.vx += (W / 2 - p.x) * 0.006;
    p.vy += (H / 2 - p.y) * 0.006;
  });

  // Integrate + damp + clamp
  graphNodes.forEach(n => {
    const p = P[n.id]; if (!p) return;
    p.vx *= 0.82; p.vy *= 0.82;
    p.x = Math.max(26, Math.min(W - 26, p.x + p.vx));
    p.y = Math.max(26, Math.min(H - 26, p.y + p.vy));
    energy += p.vx * p.vx + p.vy * p.vy;
  });

  graphSettle = energy < 0.5 ? graphSettle + 1 : 0;
}

function graphRender() {
  const ctx = graphCtx, W = graphW, H = graphH, P = graphPositions;
  if (!ctx) return;
  ctx.setTransform(graphDpr, 0, 0, graphDpr, 0, 0);

  const theme     = getTheme();
  const bgColor   = theme === "dark" ? "#0b1424" : "#f5f8fc";
  const cliColor  = "#fbbf24";
  const edgeColor = theme === "dark" ? "rgba(148,163,184,0.18)" : "rgba(15,23,42,0.10)";
  const textColor = theme === "dark" ? "#94a3b8" : "#475569";

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = bgColor; ctx.fillRect(0, 0, W, H);

  ctx.strokeStyle = edgeColor; ctx.lineWidth = 1;
  graphEdges.forEach(e => {
    const pa = P[e.source], pb = P[e.target];
    if (!pa || !pb) return;
    ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
  });

  const NODE_COLORS = {
    gateway: "#5ee1c8", host: "#60a5fa", router: "#fb923c",
    subnet: "#a78bfa", internet: "#94a3b8", self: "#34d399", switch: "#38bdf8",
  };
  const BIG_TYPES = ["ap", "gateway", "router", "subnet", "self", "internet", "switch"];
  graphNodes.forEach(n => {
    const p = P[n.id]; if (!p) return;
    const sel = n.id === graphSelected;
    const big = BIG_TYPES.includes(n.type);
    const r   = ((n.type === "gateway" || n.type === "self") ? 10 : big ? 8 : 5) + (sel ? 3 : 0);
    const fill = n.type === "ap" ? heatColor(graphHeat[n.id] ?? 0)
               : (NODE_COLORS[n.type] || cliColor);
    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    ctx.fillStyle   = fill;
    ctx.shadowColor = fill; ctx.shadowBlur = sel ? 16 : 6;
    ctx.fill(); ctx.shadowBlur = 0;
    if (sel) { ctx.strokeStyle = theme === "dark" ? "#fff" : "#0b1424"; ctx.lineWidth = 1.5; ctx.stroke(); }

    if (big || graphNodes.length < 50) {
      ctx.fillStyle = textColor;
      ctx.font      = "10px ui-monospace,monospace";
      ctx.textAlign = "center";
      const tx = Math.max(40, Math.min(W - 40, p.x));   // keep labels on-canvas
      ctx.fillText((n.label || n.id).slice(0, 16), tx, p.y + r + 11);
    }
  });
}

function onGraphClick(e) {
  const rect = e.currentTarget.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  let hit = null, best = 18 * 18;
  graphNodes.forEach(n => {
    const p = graphPositions[n.id]; if (!p) return;
    const dx = p.x - x, dy = p.y - y, d2 = dx * dx + dy * dy;
    if (d2 < best) { best = d2; hit = n; }
  });
  graphSelected = hit ? hit.id : null;
  updateGraphInfo(hit);
  if (!graphAnim) { graphSettle = 0; graphAnim = requestAnimationFrame(graphStep); }
}

function updateGraphInfo(node) {
  const box = document.getElementById("rmGraphInfo");
  if (!box) return;
  if (!node) { box.style.display = "none"; return; }
  const row = (k, v) => `<div class="rm-gi-row"><span>${k}</span><span>${v}</span></div>`;
  const mono = v => `<span class="rm-mono">${esc(v || "—")}</span>`;
  let body;
  if (node.type === "ap") {
    body = row("BSSID", mono(node.id))
         + row("Channel", node.channel ?? "—")
         + row("Signal", node.rssi != null ? node.rssi + " dBm" : "—")
         + row("Security", esc(node.security || "—"))
         + row("Clients", node.clients ?? 0);
  } else if (node.type === "subnet") {
    body = row("Subnet", mono(node.cidr))
         + (node.vlan ? row("VLAN", esc(node.vlan + (node.vlan_name ? ` (${node.vlan_name})` : ""))) : "")
         + (node.ifname ? row("Interface", esc(node.ifname)) : "");
  } else if (node.type === "router") {
    body = row("IP", mono(node.ip)) + row("Role", "L3 hop / router");
  } else if (node.type === "internet") {
    body = row("", "Internet / WAN edge");
  } else if (node.type === "self") {
    body = row("Device", "radioman (this Pi)");
  } else if (node.type === "gateway" || node.type === "host") {
    body = row("IP", mono(node.ip));
    if (node.mac)      body += row("MAC", mono(node.mac));
    if (node.vendor)   body += row("Vendor", esc(node.vendor));
    if (node.hostname) body += row("Hostname", esc(node.hostname));
    if (node.sysdescr) body += row("System", esc(String(node.sysdescr).slice(0, 70)));
  } else {
    body = row("MAC", mono(node.id))
         + row("Signal", node.rssi != null ? node.rssi + " dBm" : "—")
         + row("Vendor", esc(node.vendor || "—"))
         + row("AP", mono(node.bssid));
  }
  box.innerHTML = `<div class="rm-gi-title">${esc(node.label || node.id)}
      <button class="rm-gi-close" id="rmGiClose">✕</button></div>${body}`;
  box.style.display = "block";
  document.getElementById("rmGiClose")?.addEventListener("click", () => {
    graphSelected = null; updateGraphInfo(null);
  });
}

// ── LAN Hosts ─────────────────────────────────────────────────────────────────
function viewHosts(rows, scan = {}) {
  const sorted = [...rows].sort((a, b) => {
    const an = (a.ip || "").split(".").map(Number);
    const bn = (b.ip || "").split(".").map(Number);
    for (let i = 0; i < 4; i++) {
      if ((an[i] || 0) !== (bn[i] || 0)) return (an[i] || 0) - (bn[i] || 0);
    }
    return 0;
  });
  const scanning = !!scan.scanning;
  let scanNote = "";
  if (scan.last_error) {
    scanNote = `<span class="rm-scan-err">${esc(scan.last_error)}</span>`;
  } else if (scan.last_scan) {
    scanNote = `<span class="rm-muted" style="font-size:0.72rem">last scan: ${shortDate(new Date(scan.last_scan * 1000).toISOString())} · found ${scan.last_count ?? 0}</span>`;
  }
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${rows.length} host${rows.length !== 1 ? "s" : ""} on the LAN</span>
      <div style="display:flex;align-items:center;gap:0.75rem">
        ${scanNote}
        <button class="rm-btn rm-btn-primary" id="rmScanBtn" ${scanning ? "disabled" : ""}>
          ${scanning ? "Scanning… ⟳" : "Run nmap scan"}
        </button>
      </div>
    </div>
    ${sorted.length
      ? `<div class="dash-panel dash-panel-full">
          <div class="dash-table-scroll">
            <table class="dash-table">
              <thead><tr><th>IP</th><th>Hostname</th><th>MAC</th><th>Vendor</th><th>Method</th><th>Last seen</th></tr></thead>
              <tbody>
                ${sorted.map(r => `
                  <tr>
                    <td class="rm-mono">${deviceTag(r.device_type)}${esc(r.ip || "—")}</td>
                    <td>${esc(r.hostname || "—")}</td>
                    <td class="rm-mono rm-muted">${esc(r.mac || "—")}</td>
                    <td>${esc(r.vendor || "—")}</td>
                    <td><span class="rm-muted">${esc(r.method || "arp")}</span></td>
                    <td class="rm-muted">${r.last_seen ? shortDate(r.last_seen) : "—"}</td>
                  </tr>`).join("")}
              </tbody>
            </table>
          </div>
        </div>`
      : empty("🏠", scanning ? "Scanning the LAN…" : "No LAN hosts yet — run an nmap scan")}`;
}

function attachScanHandler() {
  const btn = document.getElementById("rmScanBtn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.textContent = "Scanning… ⟳";
    btn.disabled = true;
    try {
      // Fire-and-forget: the backend scans in the background; the periodic
      // poll picks up scan state + results. No long-blocking request here.
      await post("/api/hosts/scan");
      setTimeout(poll, 800);   // refresh to reflect "scanning" state
    } catch (e) {
      btn.textContent = "Error — retry";
      btn.disabled = false;
    }
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

function attachDeleteHandlers() {
  document.querySelectorAll(".rm-delete-network").forEach(btn => {
    btn.addEventListener("click", async () => {
      const bssid = btn.dataset.bssid;
      if (!confirm(`Delete network ${bssid} and all its records?\nThis cannot be undone.`)) return;
      btn.textContent = "Deleting…";
      btn.disabled = true;
      try {
        await del(`/api/networks/${encodeURIComponent(bssid)}`);
        btn.closest("tr").remove();
      } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
      }
    });
  });

  document.querySelectorAll(".rm-delete-client").forEach(btn => {
    btn.addEventListener("click", async () => {
      const mac = btn.dataset.mac;
      if (!confirm(`Delete client ${mac}?\nThis cannot be undone.`)) return;
      btn.textContent = "Deleting…";
      btn.disabled = true;
      try {
        await del(`/api/clients/${encodeURIComponent(mac)}`);
        btn.closest("tr").remove();
      } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
      }
    });
  });

  document.querySelectorAll(".rm-delete-ble").forEach(btn => {
    btn.addEventListener("click", async () => {
      const mac = btn.dataset.mac;
      if (!confirm(`Delete Bluetooth device ${mac}?\nThis cannot be undone.`)) return;
      btn.textContent = "Deleting…";
      btn.disabled = true;
      try {
        await del(`/api/bluetooth/${encodeURIComponent(mac)}`);
        btn.closest("tr").remove();
      } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
      }
    });
  });

  wirePurge("rmPurgeNetworks", "rmPurgeDays", "/api/networks/purge",
            d => `Delete all networks not seen in the last ${d} day${d !== 1 ? "s" : ""}?\nTheir clients and signal history will also be removed.`);
  wirePurge("rmPurgeBle", "rmPurgeBleDays", "/api/bluetooth/purge",
            d => `Delete all Bluetooth devices not seen in the last ${d} day${d !== 1 ? "s" : ""}?`);
}

// Shared purge-button wiring for the Networks and Bluetooth views.
function wirePurge(btnId, selId, endpoint, confirmMsg) {
  const purgeBtn = document.getElementById(btnId);
  if (!purgeBtn) return;
  purgeBtn.addEventListener("click", async () => {
    const days = parseInt(document.getElementById(selId)?.value || "7", 10);
    if (!confirm(confirmMsg(days))) return;
    purgeBtn.textContent = "Purging…";
    purgeBtn.disabled = true;
    try {
      const res = await post(endpoint, { days });
      purgeBtn.textContent = `Purged ${res.purged ?? 0}`;
      poll();
    } catch (e) {
      purgeBtn.textContent = "Error";
      purgeBtn.disabled = false;
    }
  });
}

async function del(path) {
  const r = await fetch(API + path, { method: "DELETE" });
  return r.json();
}

// ── Active / offensive testing (authorized engagements only) ──────────────────
// Renders a <details open> attribute that honours the user's last manual toggle
// (tracked in accState) rather than resetting on every poll re-render.
function accOpen(key, def) {
  return (key in accState ? accState[key] : def) ? "open" : "";
}

// A signature of only the data the CURRENT sub-tab actually shows, so the 5s
// poll re-renders the Active view only when something visible changed (avoids
// the "refreshing on its own" flicker that collapsed expanded panels).
function activeSig() {
  if (!activeCache) return "";
  const [off, scope, audit, nets, rogue, loot, lab, eng] = activeCache;
  let d;
  switch (activeTab) {
    case "rogue": d = [rogue, loot]; break;
    case "scope": d = [scope, lab, eng, !!off.enabled]; break;
    case "audit": d = [audit]; break;
    default:      d = [!!off.enabled, !!off.scanning, scope, nets, rogue.running, rogue.armed];
  }
  return activeTab + "|" + JSON.stringify(d);
}

function viewActive(off, scope, audit, nets, rogue, loot, lab, engagements) {
  const enabled  = !!off.enabled;
  const scanning = !!off.scanning;
  lab = lab || [];
  engagements = engagements || [];
  const ssidScope  = new Set(scope.filter(s => s.kind === "ssid").map(s => s.target));
  const bssidScope = new Set(scope.filter(s => s.kind === "bssid").map(s => (s.target || "").toUpperCase()));

  const banner = `
    <div class="rm-roe-banner ${enabled ? "rm-roe-armed" : ""}">
      <span class="rm-roe-icon">⚠</span>
      <div>
        <strong>Authorized testing only.</strong>
        Active actions only run against targets in scope, and every attempt is logged.
        ${enabled
          ? `<span class="rm-roe-state rm-roe-on">OFFENSIVE MODE ON</span>`
          : `<span class="rm-roe-state rm-roe-off">OFFENSIVE MODE OFF</span> — set <code>[offensive] enabled=true</code> in <code>radioman.conf</code> and restart to use.`}
      </div>
    </div>`;

  // Engagement context — set the authorization ref / label / expiry ONCE, then
  // one-tap authorize any AP in range. Used by every add path (one-tap, manual,
  // bulk, lab-apply) so authorization is fast without dropping the attestation.
  const engChips = engagements.length
    ? engagements.map(e => `<span class="rm-eng-chip">${esc(e.engagement)} <span class="rm-muted">(${e.count})</span>
        <button class="rm-eng-end" data-engagement="${esc(e.engagement)}" title="End engagement — clears its ${e.count} scope entr${e.count === 1 ? "y" : "ies"}">×</button></span>`).join("")
    : `<span class="rm-muted">no active engagements</span>`;
  const contextCard = `
    <details class="dash-panel dash-panel-full rm-acc" data-acc="engagement" style="margin-top:1rem" ${accOpen("engagement", engagements.length === 0)}>
      <summary class="rm-acc-summary">
        <span><strong>Engagement context</strong></span>
        <span class="rm-muted rm-acc-hint">${engagements.length ? engagements.length + " active — tap to edit" : "set authorization once →"}</span>
      </summary>
      <div class="rm-acc-body">
        <div class="rm-ignore-form">
          <input class="rm-ignore-input" id="rmEngAuth" placeholder="Authorization ref (ticket / client / RoE id)" maxlength="80" style="flex:2;min-width:220px">
          <input class="rm-ignore-input" id="rmEngName" placeholder="Engagement label (optional)" maxlength="80" style="flex:1;min-width:140px">
          <select class="rm-purge-select" id="rmEngTtl" title="Auto-expire scope entries added with this context">
            <option value="0">No expiry</option>
            <option value="4">Expires 4h</option>
            <option value="8">Expires 8h</option>
            <option value="24">Expires 24h</option>
            <option value="72">Expires 72h</option>
          </select>
        </div>
        <div class="rm-eng-chips">${engChips}</div>
      </div>
    </details>`;

  // Live, in-scope APs (by exact BSSID or SSID membership) → one-tap deauth.
  const liveTargets = nets.filter(n =>
    bssidScope.has((n.bssid || "").toUpperCase()) || ssidScope.has(n.ssid));
  const targetsCard = `
    <div class="dash-panel dash-panel-full">
      <div class="dash-panel-header"><h3>Authorized targets — live</h3>
        <span class="rm-muted">${liveTargets.length} in range &amp; in scope</span></div>
      <div class="dash-table-scroll">
        <table class="dash-table rm-cards-sm">
          <thead><tr><th>SSID</th><th>BSSID</th><th>CH</th><th>Signal</th><th>Clients</th><th></th></tr></thead>
          <tbody>${liveTargets.length ? liveTargets.map(n => `
            <tr>
              <td data-label="SSID" class="rm-table-ssid">${esc(n.ssid || "—")}</td>
              <td data-label="BSSID" class="rm-mono">${esc(n.bssid)}</td>
              <td data-label="CH">${n.channel ?? "—"}</td>
              <td data-label="Signal">${rssiCell(n.rssi)}</td>
              <td data-label="Clients">${n.clients ?? 0}</td>
              <td data-label=""><button class="rm-deauth-btn" data-bssid="${esc(n.bssid)}" ${enabled && scanning ? "" : "disabled"}
                    title="${enabled ? (scanning ? "Deauth this AP's clients to force a handshake" : "Start a scan first — monitor mode must be active") : "Enable offensive mode first"}">Deauth</button></td>
            </tr>`).join("") : `<tr><td colspan="6" class="rm-muted">No in-scope APs in range yet — open the <strong>Scope</strong> tab, or tap <strong>+ Add</strong> on an AP below.</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`;

  // In range but NOT yet authorized → one-tap "Authorize" (uses engagement context).
  const unscoped = nets
    .filter(n => n.bssid &&
      !bssidScope.has((n.bssid || "").toUpperCase()) &&
      !ssidScope.has(n.ssid))
    .sort((a, b) => (b.rssi ?? -100) - (a.rssi ?? -100))
    .slice(0, 30);
  const discoverCard = `
    <div class="dash-panel dash-panel-full" style="margin-top:1rem">
      <div class="dash-panel-header"><h3>In range — not in scope</h3>
        <span class="rm-muted">${unscoped.length} discovered AP${unscoped.length !== 1 ? "s" : ""}</span></div>
      <div class="dash-table-scroll">
        <table class="dash-table rm-cards-sm">
          <thead><tr><th>SSID</th><th>BSSID</th><th>CH</th><th>Signal</th><th></th></tr></thead>
          <tbody>${unscoped.length ? unscoped.map(n => `
            <tr>
              <td data-label="SSID" class="rm-table-ssid">${esc(n.ssid || "—")}</td>
              <td data-label="BSSID" class="rm-mono">${esc(n.bssid)}</td>
              <td data-label="CH">${n.channel ?? "—"}</td>
              <td data-label="Signal">${rssiCell(n.rssi)}</td>
              <td data-label="">
                <button class="rm-authz-btn" data-kind="bssid" data-target="${esc(n.bssid)}" title="Add this AP (BSSID) to scope using the engagement context above">+ BSSID</button>
                ${n.ssid ? `<button class="rm-authz-btn" data-kind="ssid" data-target="${esc(n.ssid)}" title="Authorize the whole SSID — every AP broadcasting this name becomes in-scope">+ SSID</button>` : ""}
              </td>
            </tr>`).join("") : `<tr><td colspan="5" class="rm-muted">No un-scoped APs in range — run a scan to discover.</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`;

  // Rogue AP control card
  const ssidOptions = [...ssidScope];
  const rg = rogue || {};
  const stateChip = rg.running
    ? `<span class="rm-roe-state rm-roe-on">RUNNING — ${esc(rg.ssid)}</span>`
    : rg.armed
      ? `<span class="rm-roe-state" style="background:var(--rm-amber);color:#1a1205">ARMED — ${esc(rg.ssid)}</span>`
      : `<span class="rm-roe-state rm-roe-off">IDLE</span>`;
  const rogueCard = `
    <div class="dash-panel dash-panel-full" style="margin-top:1rem">
      <div class="dash-panel-header"><h3>Rogue AP — evil twin</h3>${stateChip}</div>
      <div class="rm-ignore-form">
        <select class="rm-purge-select" id="rmRogueSsid" ${rg.running ? "disabled" : ""}>
          ${ssidOptions.length ? ssidOptions.map(s => `<option value="${esc(s)}" ${s === rg.ssid ? "selected" : ""}>${esc(s)}</option>`).join("")
                               : `<option value="">— add an SSID to scope first —</option>`}
        </select>
        <input class="rm-ignore-input" id="rmRogueAuth" placeholder="Authorization ref" maxlength="80" value="${esc(rg.authref || "")}" ${rg.armed ? "disabled" : ""}>
        <input class="rm-ignore-input rm-mono" id="rmRogueCh" placeholder="ch" maxlength="3" style="width:60px" value="${rg.channel || 6}" ${rg.running ? "disabled" : ""}>
        <label class="rm-roe-cred-toggle" title="Phishing portal — captures credentials from anyone who connects, in or out of scope. Off by default.">
          <input type="checkbox" id="rmRogueCreds" ${rg.capture_creds ? "checked" : ""} ${rg.armed ? "disabled" : ""}> capture credentials
        </label>
        ${rg.armed
          ? (rg.running
              ? `<button class="rm-btn rm-btn-danger" id="rmRogueStop">Stop AP</button>`
              : `<button class="rm-btn rm-btn-primary" id="rmRogueStart">Start AP</button>
                 <button class="rm-btn" id="rmRogueDisarm">Disarm</button>`)
          : `<button class="rm-btn rm-btn-primary" id="rmRogueArm" ${enabled && ssidOptions.length ? "" : "disabled"}>Arm</button>`}
      </div>
      ${rg.running ? `<div class="rm-muted" style="padding:0 1.25rem 0.5rem">
        Live on <span class="rm-mono">${esc(rg.iface)}</span> ch ${rg.channel} ·
        ${rg.capture_creds ? "credential portal ON" : "association test (no credential capture)"} ·
        ${rg.clients ?? 0} client(s) seen, ${rg.captures ?? 0} submission(s)</div>` : ""}
    </div>`;

  // Loot (associations + masked credential captures)
  const clients = (loot.clients || []);
  const caps = (loot.captures || []);
  const lootCard = (clients.length || caps.length) ? `
    <div class="dash-panel dash-panel-full" style="margin-top:1rem">
      <div class="dash-panel-header"><h3>Rogue AP loot</h3>
        <button class="rm-purge-btn" id="rmRogueClear">Clear loot</button></div>
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr><th>Time</th><th>SSID</th><th>Client</th><th>Username</th><th>Password</th></tr></thead>
          <tbody>
            ${caps.map(c => `<tr>
              <td class="rm-muted">${shortDate(c.ts)}</td><td>${esc(c.ssid || "")}</td>
              <td class="rm-mono">${esc(c.client_mac || c.client_ip || "—")}</td>
              <td class="rm-mono">${esc(c.username || "")}</td>
              <td class="rm-mono">${esc(c.password || "")}</td></tr>`).join("")}
            ${clients.map(c => `<tr>
              <td class="rm-muted">${shortDate(c.ts)}</td><td>${esc(c.ssid || "")}</td>
              <td class="rm-mono">${esc(c.mac || c.ip || "—")}</td>
              <td class="rm-muted" colspan="2">associated${c.user_agent ? " · " + esc(c.user_agent.slice(0, 40)) : ""}</td></tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>` : "";

  // My Lab — saved owned networks, one-click apply to scope.
  const labRows = lab.length ? lab.map(t => `
    <tr>
      <td><span class="rm-scope-kind">${esc(t.kind)}</span></td>
      <td class="rm-mono">${esc(t.target)}</td>
      <td class="rm-muted">${esc(t.note || "")}</td>
      <td><button class="rm-delete-btn rm-lab-del" data-kind="${esc(t.kind)}" data-target="${esc(t.target)}">Remove</button></td>
    </tr>`).join("") : `<tr><td colspan="4" class="rm-muted">No lab targets saved. Add your own networks here to scope them in one click.</td></tr>`;
  const labCard = `
    <div class="dash-panel dash-panel-full" style="margin-top:1rem">
      <div class="dash-panel-header"><h3>My Lab — owned networks</h3>
        <button class="rm-btn rm-btn-primary" id="rmLabApply" ${lab.length ? "" : "disabled"}
          title="Add every saved lab target to scope using the engagement context above">Apply lab to scope</button></div>
      <div class="rm-ignore-form">
        <select class="rm-purge-select" id="rmLabKind">
          <option value="bssid">BSSID (AP)</option>
          <option value="client">Client MAC</option>
          <option value="ssid">SSID</option>
          <option value="ip">IP / CIDR</option>
        </select>
        <input class="rm-ignore-input rm-mono" id="rmLabTarget" placeholder="AA:BB:CC:DD:EE:FF" maxlength="64" spellcheck="false">
        <input class="rm-ignore-input" id="rmLabNote" placeholder="Note (e.g. home router)" maxlength="80">
        <button class="rm-btn" id="rmLabAdd">Save</button>
      </div>
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr><th>Kind</th><th>Target</th><th>Note</th><th></th></tr></thead>
          <tbody>${labRows}</tbody>
        </table>
      </div>
    </div>`;

  // Scope management
  const scopeRows = scope.length ? scope.map(s => `
    <tr class="${s.expired ? "rm-scope-expired" : ""}">
      <td><span class="rm-scope-kind">${esc(s.kind)}</span></td>
      <td class="rm-mono">${esc(s.target)}</td>
      <td>${esc(s.authref || "—")}</td>
      <td>${s.engagement ? esc(s.engagement) : "<span class='rm-muted'>—</span>"}</td>
      <td class="rm-muted">${s.expired ? "expired" : (s.expires ? "→ " + shortDate(s.expires) : "—")}</td>
      <td><button class="rm-delete-btn rm-scope-del" data-kind="${esc(s.kind)}" data-target="${esc(s.target)}">Remove</button></td>
    </tr>`).join("") : `<tr><td colspan="6" class="rm-muted">Scope is empty — nothing is authorized.</td></tr>`;
  const scopePanel = `
    <div class="dash-panel dash-panel-full" style="margin-top:1rem">
      <div class="dash-panel-header"><h3>Rules of Engagement — Scope</h3>
        <span class="rm-muted">${scope.length} target${scope.length !== 1 ? "s" : ""}</span></div>
      <div class="rm-ignore-form">
        <select class="rm-purge-select" id="rmScopeKind">
          <option value="bssid">BSSID (AP)</option>
          <option value="client">Client MAC</option>
          <option value="ssid">SSID</option>
          <option value="ip">IP / CIDR</option>
        </select>
        <input class="rm-ignore-input rm-mono" id="rmScopeTarget" placeholder="AA:BB:CC:DD:EE:FF" maxlength="64" spellcheck="false">
        <button class="rm-btn rm-btn-primary" id="rmScopeAdd" title="Adds using the engagement context above">Add</button>
      </div>
      <div class="rm-ignore-form" style="padding-top:0">
        <textarea class="rm-ignore-input" id="rmScopeBulk" rows="2" style="flex:1;min-width:240px;resize:vertical"
          placeholder="Bulk paste from RoE doc — one per line (MAC→AP, IP/CIDR→ip, else SSID). Optional 'ssid:' / 'bssid:' prefix."></textarea>
        <button class="rm-btn" id="rmScopeBulkAdd">Import</button>
      </div>
      <div class="rm-muted" style="padding:0 1.25rem 0.6rem;font-size:0.78rem">
        Authorization ref, engagement label &amp; expiry come from <strong>Engagement context</strong> above.
      </div>
      <div class="dash-table-scroll">
        <table class="dash-table">
          <thead><tr><th>Kind</th><th>Target</th><th>Auth ref</th><th>Engagement</th><th>Expiry</th><th></th></tr></thead>
          <tbody>${scopeRows}</tbody>
        </table>
      </div>
    </div>`;

  const auditRows = audit.length ? audit.map(a => `
    <div class="rm-log-row">
      <span class="rm-log-ts">${shortDate(a.ts)}</span>
      <span class="rm-log-lvl rm-log-lvl-${esc(a.level)}">${a.level === "active" ? "ALLOW" : "DENY"}</span>
      <span>${esc(a.message)}</span>
    </div>`).join("") : `<div class="rm-muted" style="padding:1rem">No active actions recorded yet.</div>`;
  const auditPanel = `
    <div class="dash-panel dash-panel-full" style="margin-top:1rem">
      <div class="dash-panel-header"><h3>Audit Trail</h3><span class="rm-muted">${audit.length} entr${audit.length !== 1 ? "ies" : "y"}</span></div>
      <div class="rm-log-list">${auditRows}</div>
    </div>`;

  // Guided stepper — shows where you are in the flow without forcing a wizard.
  const stepState = (done, current) => done ? "done" : (current ? "current" : "todo");
  const s1 = enabled, s2 = scope.length > 0;
  const stepper = `
    <div class="rm-stepper">
      <div class="rm-step rm-step-${stepState(s1, !s1)}"><span class="rm-step-n">${s1 ? "✓" : "1"}</span>Enable</div>
      <span class="rm-step-sep"></span>
      <div class="rm-step rm-step-${stepState(s2, s1 && !s2)}"><span class="rm-step-n">${s2 ? "✓" : "2"}</span>Scope a target</div>
      <span class="rm-step-sep"></span>
      <div class="rm-step rm-step-${stepState(false, s1 && s2)}"><span class="rm-step-n">3</span>Act</div>
    </div>`;

  // Sub-tabs — one focused screen at a time instead of a 9-panel wall.
  const rg2 = rogue || {};
  const rogueBadge = rg2.running ? "●" : (rg2.armed ? "◐" : null);
  const subtab = (id, label, badge) =>
    `<button class="rm-subtab ${activeTab === id ? "active" : ""}" data-subtab="${id}">${label}${(badge !== null && badge !== "") ? ` <span class="rm-subtab-badge">${badge}</span>` : ""}</button>`;
  const subnav = `
    <div class="rm-subnav">
      ${subtab("targets", "Targets", liveTargets.length)}
      ${subtab("rogue", "Rogue AP", rogueBadge)}
      ${subtab("scope", "Scope", scope.length)}
      ${subtab("audit", "Audit", audit.length)}
    </div>`;

  let body;
  switch (activeTab) {
    case "rogue": body = (CAPS.rogue_ap ? "" : hwGate("Rogue AP needs a USB Wi-Fi adapter with AP-mode support, plus hostapd + dnsmasq. Scope setup still works here.")) + rogueCard + (lootCard || ""); break;
    case "scope": body = scopePanel + labCard; break;
    case "audit": body = auditPanel; break;
    default:      body = (CAPS.wifi_monitor ? "" : hwGate(GATE_MONITOR)) + targetsCard + discoverCard;   // "targets"
  }

  return banner + contextCard + stepper + subnav + body;
}

// Read the shared engagement context (authref / label / ttl) used by every
// scope-add path. Returns null (and flags the field) if no authref is set.
function engagementContext() {
  const authEl = document.getElementById("rmEngAuth");
  const authref = (authEl?.value || "").trim();
  if (!authref) {
    if (authEl) { authEl.style.borderColor = "var(--rm-red)"; authEl.focus(); }
    return null;
  }
  return {
    authref,
    engagement: (document.getElementById("rmEngName")?.value || "").trim(),
    ttl_hours: parseFloat(document.getElementById("rmEngTtl")?.value || "0") || 0,
  };
}

function attachActiveHandlers() {
  // Sub-tab switching — instant re-render from the cached payload (no refetch).
  document.querySelectorAll(".rm-subtab").forEach(btn => {
    btn.addEventListener("click", () => {
      activeTab = btn.dataset.subtab;
      if (activeCache) {
        lastActiveSig = activeSig();
        document.getElementById("rmMain").innerHTML = viewActive(...activeCache);
        attachActiveHandlers();
      } else {
        poll();
      }
    });
  });

  // Remember which collapsible panels the user expanded, so a later re-render
  // (when data changes) doesn't snap them shut.
  document.querySelectorAll("details.rm-acc[data-acc]").forEach(d => {
    d.addEventListener("toggle", () => { accState[d.dataset.acc] = d.open; });
  });

  const kindSel = document.getElementById("rmScopeKind");
  const targetEl = document.getElementById("rmScopeTarget");
  if (kindSel && targetEl) {
    const ph = { bssid: "AA:BB:CC:DD:EE:FF", client: "AA:BB:CC:DD:EE:FF",
                 ssid: "Target-SSID", ip: "192.168.1.0/24" };
    kindSel.addEventListener("change", () => { targetEl.placeholder = ph[kindSel.value] || ""; });
  }

  const addBtn = document.getElementById("rmScopeAdd");
  if (addBtn) addBtn.addEventListener("click", async () => {
    const kind   = kindSel?.value || "bssid";
    const target = (targetEl?.value || "").trim();
    if (!target) { targetEl?.focus(); return; }
    const ctx = engagementContext();
    if (!ctx) return;
    addBtn.textContent = "Adding…"; addBtn.disabled = true;
    try {
      const r = await post("/api/scope", { kind, target, ...ctx });
      if (r.error) alert(r.error); else poll();
    } catch (e) { /* ignore */ }
    addBtn.textContent = "Add"; addBtn.disabled = false;
  });

  const bulkBtn = document.getElementById("rmScopeBulkAdd");
  if (bulkBtn) bulkBtn.addEventListener("click", async () => {
    const text = (document.getElementById("rmScopeBulk")?.value || "").trim();
    if (!text) return;
    const ctx = engagementContext();
    if (!ctx) { alert("Set an Authorization ref in Engagement context above before importing."); return; }
    bulkBtn.textContent = "Importing…"; bulkBtn.disabled = true;
    try {
      const r = await post("/api/scope/bulk", { text, ...ctx });
      if (r.error) alert(r.error);
      else { if (r.skipped?.length) alert(`Imported ${r.added}. Skipped (bad MAC): ${r.skipped.join(", ")}`); poll(); }
    } catch (e) { /* ignore */ }
    bulkBtn.textContent = "Import"; bulkBtn.disabled = false;
  });

  // One-tap authorize from the "in range — not in scope" list.
  document.querySelectorAll(".rm-authz-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const { kind, target } = btn.dataset;
      const ctx = engagementContext();
      if (!ctx) { alert("Set an Authorization ref in Engagement context above first."); return; }
      btn.textContent = "…"; btn.disabled = true;
      try {
        const r = await post("/api/scope", { kind, target, ...ctx });
        if (r.error) { alert(r.error); btn.textContent = "+ " + kind.toUpperCase(); btn.disabled = false; }
        else poll();
      } catch (e) { btn.disabled = false; }
    });
  });

  // End an engagement — clears all its scope entries.
  document.querySelectorAll(".rm-eng-end").forEach(btn => {
    btn.addEventListener("click", async () => {
      const eng = btn.dataset.engagement;
      if (!confirm(`End engagement "${eng}"?\n\nThis removes every scope entry tagged with it — those targets will no longer be authorized.`)) return;
      btn.disabled = true;
      try { await del(`/api/scope/engagement?engagement=${encodeURIComponent(eng)}`); poll(); }
      catch (e) { btn.disabled = false; }
    });
  });

  // My Lab — add / remove / apply.
  const labAdd = document.getElementById("rmLabAdd");
  if (labAdd) labAdd.addEventListener("click", async () => {
    const kind   = document.getElementById("rmLabKind")?.value || "bssid";
    const target = (document.getElementById("rmLabTarget")?.value || "").trim();
    const note   = (document.getElementById("rmLabNote")?.value || "").trim();
    if (!target) return;
    labAdd.disabled = true;
    try {
      const r = await post("/api/lab", { kind, target, note });
      if (r.error) alert(r.error); else poll();
    } catch (e) { /* ignore */ }
    labAdd.disabled = false;
  });

  document.querySelectorAll(".rm-lab-del").forEach(btn => {
    btn.addEventListener("click", async () => {
      const { kind, target } = btn.dataset;
      btn.disabled = true;
      try { await del(`/api/lab?kind=${encodeURIComponent(kind)}&target=${encodeURIComponent(target)}`); poll(); }
      catch (e) { btn.disabled = false; }
    });
  });

  const labApply = document.getElementById("rmLabApply");
  if (labApply) labApply.addEventListener("click", async () => {
    const ctx = engagementContext() || { authref: "owned-lab", engagement: "lab", ttl_hours: 0 };
    if (!confirm("Add all saved lab targets to scope?")) return;
    labApply.textContent = "Applying…"; labApply.disabled = true;
    try {
      const r = await post("/api/lab/apply", ctx);
      if (r.error) alert(r.error); else poll();
    } catch (e) { /* ignore */ }
    labApply.textContent = "Apply lab to scope"; labApply.disabled = false;
  });

  document.querySelectorAll(".rm-scope-del").forEach(btn => {
    btn.addEventListener("click", async () => {
      const { kind, target } = btn.dataset;
      btn.disabled = true;
      try {
        await del(`/api/scope?kind=${encodeURIComponent(kind)}&target=${encodeURIComponent(target)}`);
        poll();
      } catch (e) { btn.disabled = false; }
    });
  });

  document.querySelectorAll(".rm-deauth-btn").forEach(btn => {
    if (btn.disabled) return;
    btn.addEventListener("click", async () => {
      const bssid = btn.dataset.bssid;
      if (!confirm(`Send a targeted deauth to ${bssid}?\n\nThis disconnects that AP's clients to force a handshake. Authorized, in-scope targets only — this action is logged.`)) return;
      btn.textContent = "Deauthing…"; btn.disabled = true;
      try {
        const r = await post("/api/attack/deauth", { bssid, reason: "handshake capture" });
        if (r.ok) { btn.textContent = "Sent ✓"; setTimeout(poll, 1500); }
        else { alert(r.error || "Deauth failed"); btn.textContent = "Deauth"; btn.disabled = false; }
      } catch (e) { btn.textContent = "Error"; btn.disabled = false; }
    });
  });

  // ── Rogue AP ──
  const armBtn = document.getElementById("rmRogueArm");
  if (armBtn) armBtn.addEventListener("click", async () => {
    const ssid = document.getElementById("rmRogueSsid")?.value || "";
    const authref = (document.getElementById("rmRogueAuth")?.value || "").trim();
    const creds = !!document.getElementById("rmRogueCreds")?.checked;
    if (!ssid) { alert("Select an in-scope SSID."); return; }
    if (!authref) { alert("Enter an authorization reference to arm."); return; }
    let msg = `Arm a rogue AP impersonating "${ssid}"?\n\nClients in range may connect to it — including devices outside your scope. You are attesting you are authorized to impersonate this SSID for engagement: ${authref}.`;
    if (creds) msg += `\n\n⚠ CREDENTIAL CAPTURE IS ON — the portal will capture credentials entered by ANY device that connects.`;
    if (!confirm(msg)) return;
    armBtn.textContent = "Arming…"; armBtn.disabled = true;
    try {
      const r = await post("/api/rogueap/arm", { ssid, authref, capture_creds: creds, acknowledge: true });
      if (r.error) alert(r.error);
      poll();
    } catch (e) { armBtn.disabled = false; }
  });

  const startBtn = document.getElementById("rmRogueStart");
  if (startBtn) startBtn.addEventListener("click", async () => {
    const channel = parseInt(document.getElementById("rmRogueCh")?.value || "6", 10);
    startBtn.textContent = "Starting…"; startBtn.disabled = true;
    try {
      const r = await post("/api/rogueap/start", { channel });
      if (r.error) { alert(r.error); }
      poll();
    } catch (e) { startBtn.disabled = false; }
  });

  const stopBtn = document.getElementById("rmRogueStop");
  if (stopBtn) stopBtn.addEventListener("click", async () => {
    stopBtn.textContent = "Stopping…"; stopBtn.disabled = true;
    try { await post("/api/rogueap/stop", {}); poll(); } catch (e) { stopBtn.disabled = false; }
  });

  const disarmBtn = document.getElementById("rmRogueDisarm");
  if (disarmBtn) disarmBtn.addEventListener("click", async () => {
    try { await post("/api/rogueap/disarm", {}); poll(); } catch (e) { /* ignore */ }
  });

  const clearLoot = document.getElementById("rmRogueClear");
  if (clearLoot) clearLoot.addEventListener("click", async () => {
    if (!confirm("Delete all rogue-AP loot (associations + captured submissions)?")) return;
    try { await del("/api/rogueap/loot"); poll(); } catch (e) { /* ignore */ }
  });
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

// Explain-and-disable notice for features that need hardware not currently
// attached (USB Wi-Fi adapter, GPS, PiSugar). Honest > empty tables in the field.
function hwGate(msg) {
  return `
    <div class="rm-hwgate">
      <span class="rm-hwgate-icon">🔌</span>
      <div>
        <div class="rm-hwgate-title">Hardware required</div>
        <div class="rm-muted">${msg}</div>
      </div>
    </div>`;
}
const GATE_MONITOR = "Needs a USB Wi-Fi adapter in monitor mode (e.g. Alfa AWUS036ACH) — the Pi's internal Synaptics radio can't capture handshakes or see Wi-Fi clients. Managed-mode AP scanning still works on the Networks tab.";

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
  const g = groupForView(view);
  currentGroup = g.id;
  currentView = view;
  groupLastView[g.id] = view;
  highlightNav();
  renderGroupNav();
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

  const cong = networkCongestion(networks);
  const heatPanel = cong.length ? `
      <div class="dash-panel rm-chart-panel rm-chart-full">
        <div class="dash-panel-header"><h3>Network Congestion</h3>
          <span class="rm-muted" style="font-size:0.72rem">who's crowding the air — click for signal history</span>
        </div>
        <div class="rm-settings-body rm-heat-list">
          ${cong.map(({ net, norm }) => {
            const mine = MY_BSSID && (net.bssid || "").toUpperCase() === MY_BSSID;
            return `
            <button class="rm-heat-row rm-rssi-row-btn" data-bssid="${esc(net.bssid)}" data-ssid="${esc(net.ssid || net.bssid)}" data-channel="${net.channel ?? ""}" data-security="${esc(net.security || "")}" data-rssi="${net.rssi ?? ""}">
              <span class="rm-heat-name">${mine ? '<span class="rm-mine-star">★</span> ' : ""}${esc(net.ssid || "(hidden)")}</span>
              <span class="rm-heat-ch">ch ${net.channel ?? "—"}</span>
              <span class="rm-heat-sig rm-mono">${net.rssi ?? "—"} dBm</span>
              <span class="rm-heat-bar"><span class="rm-heat-fill" style="width:${Math.round(norm * 100)}%;background:${heatColor(norm)}"></span></span>
            </button>`;
          }).join("")}
        </div>
      </div>` : "";

  const fiveGhzEmpty = `
      <div class="dash-panel rm-chart-panel rm-chart-full">
        <div class="dash-panel-header"><h3>5 GHz</h3></div>
        <div class="rm-settings-body">
          <p class="rm-muted" style="margin:0">No 5 GHz networks detected. The Pi's internal radio is
          <strong>2.4 GHz only</strong> — 5 GHz analysis appears here automatically once a dual-band USB
          adapter (e.g. Alfa AWUS036ACH) is connected and scanning.</p>
        </div>
      </div>`;
  return `
    <div class="rm-action-bar">
      <span class="rm-muted">${total} network${total !== 1 ? "s" : ""} in database</span>
      <span class="rm-muted rm-mono" style="font-size:0.72rem">click a network in the congestion list for signal history</span>
    </div>
    <div class="rm-stats-grid">
      ${has24 ? `
      <div class="dash-panel rm-chart-panel rm-chart-full" id="rmChartChannel24Wrap">
        <div class="dash-panel-header"><h3>2.4 GHz Channel Analyzer</h3>
          <span class="rm-muted" style="font-size:0.72rem">WiFi Analyzer</span>
        </div>
        <div class="rm-chart-wrap">
          <canvas id="rmChannelChart24" class="rm-chart-canvas rm-chart-tall"></canvas>
          <div class="rm-chart-legend" id="rmChannelLegend24"></div>
        </div>
      </div>
      <div class="dash-panel rm-chart-panel rm-chart-full">
        <div class="dash-panel-header"><h3>2.4 GHz Channel Congestion</h3>
          <span class="rm-mono rm-reco" id="rmReco24"></span>
        </div>
        <div class="rm-chart-wrap">
          <canvas id="rmCongestion24" class="rm-chart-canvas"></canvas>
        </div>
      </div>` : ""}
      ${heatPanel}
      ${has5 ? `
      <div class="dash-panel rm-chart-panel rm-chart-full">
        <div class="dash-panel-header"><h3>5 GHz Channel Analyzer</h3></div>
        <div class="rm-chart-wrap">
          <canvas id="rmChannelChart5" class="rm-chart-canvas rm-chart-tall"></canvas>
          <div class="rm-chart-legend" id="rmChannelLegend5"></div>
        </div>
      </div>
      <div class="dash-panel rm-chart-panel rm-chart-full">
        <div class="dash-panel-header"><h3>5 GHz Channel Congestion</h3>
          <span class="rm-mono rm-reco" id="rmReco5"></span>
        </div>
        <div class="rm-chart-wrap">
          <canvas id="rmCongestion5" class="rm-chart-canvas"></canvas>
        </div>
      </div>` : fiveGhzEmpty}
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

  const ctxCon24 = document.getElementById("rmCongestion24");
  const ctxCon5  = document.getElementById("rmCongestion5");
  if (ctxCon24 && nets24.length) drawCongestion(ctxCon24, nets24, "2.4", textClr, lineClr, "rmReco24");
  if (ctxCon5  && nets5.length)  drawCongestion(ctxCon5,  nets5,  "5",   textClr, lineClr, "rmReco5");
}

// ── Channel congestion (WiFiman "best channel" recommendation) ────────────────
const CH24_RECO = [1, 6, 11];                          // non-overlapping 2.4 GHz
const CH5_STD   = [36, 40, 44, 48, 149, 153, 157, 161]; // common UNII-1/3 channels

// Signal weight: a strong AP interferes more than a weak one.
// -30 dBm → 1.0, -95 dBm → ~0.0
function sigWeight(rssi) {
  return Math.max(0.1, Math.min(1, ((rssi ?? -90) + 95) / 65));
}

// Per-network congestion contribution: a loud AP sitting on a crowded channel
// scores hot. Returns networks sorted hottest-first with a normalised score.
function networkCongestion(nets) {
  const aps = (nets || []).filter(n => n.channel >= 1 && n.channel <= 14);
  const overlap = d => Math.max(0, 1 - d / 5);
  const congAt = ch => aps.reduce((s, n) => s + sigWeight(n.rssi) * overlap(Math.abs(ch - n.channel)), 0);
  const scored = aps.map(n => ({ net: n, score: sigWeight(n.rssi) * congAt(n.channel) }));
  scored.sort((a, b) => b.score - a.score);
  const max = Math.max(1, ...scored.map(s => s.score));
  scored.forEach(s => { s.norm = s.score / max; });
  return scored;
}

// Cool (green) → hot (red)
function heatColor(norm) {
  const r = Math.round(52  + (248 - 52)  * norm);
  const g = Math.round(211 + (113 - 211) * norm);
  const b = Math.round(153 + (113 - 153) * norm);
  return `rgb(${r},${g},${b})`;
}

// Returns { bars:[{ch,score}], best } — congestion score per channel,
// accounting for adjacent-channel overlap, weighted by signal strength.
function channelCongestion(nets, band) {
  if (band === "2.4") {
    const aps = nets.filter(n => n.channel >= 1 && n.channel <= 14);
    const overlap = d => Math.max(0, 1 - d / 5);   // 20 MHz spans ~±4 channels
    const bars = [];
    for (let c = 1; c <= 13; c++) {
      bars.push({ ch: c, score: aps.reduce(
        (s, n) => s + sigWeight(n.rssi) * overlap(Math.abs(c - n.channel)), 0) });
    }
    let best = CH24_RECO[0], bestScore = Infinity;
    CH24_RECO.forEach(c => {
      const b = bars.find(x => x.ch === c);
      if (b && b.score < bestScore) { bestScore = b.score; best = c; }
    });
    return { bars, best };
  }
  const aps      = nets.filter(n => n.channel >= 36);
  const observed = [...new Set(aps.map(n => n.channel))];
  const cand     = [...new Set([...CH5_STD, ...observed])].sort((a, b) => a - b);
  const overlap  = d => (d === 0 ? 1 : d <= 2 ? 0.3 : 0); // mostly non-overlapping
  const bars = cand.map(c => ({ ch: c, score: aps.reduce(
    (s, n) => s + sigWeight(n.rssi) * overlap(Math.abs(c - n.channel)), 0) }));
  // Recommend among standard channels when any are in range, else any candidate.
  const pool = CH5_STD.filter(c => cand.includes(c));
  let best = (pool[0] ?? cand[0]), bestScore = Infinity;
  (pool.length ? pool : cand).forEach(c => {
    const b = bars.find(x => x.ch === c);
    if (b && b.score < bestScore) { bestScore = b.score; best = c; }
  });
  return { bars, best };
}

function drawCongestion(canvas, nets, band, textClr, lineClr, recoElId) {
  const { W, H, ctx } = _fitCanvas(canvas);
  const mg = { top: 14, right: 14, bottom: 28, left: 30 };
  const pW = W - mg.left - mg.right;
  const pH = H - mg.top  - mg.bottom;
  ctx.clearRect(0, 0, W, H);

  const { bars, best } = channelCongestion(nets, band);
  const maxScore = Math.max(1, ...bars.map(b => b.score));
  const barW = pW / bars.length;

  // gridlines
  ctx.strokeStyle = lineClr; ctx.lineWidth = 1;
  [0.5, 1].forEach(f => {
    const y = mg.top + pH - f * pH;
    ctx.beginPath(); ctx.moveTo(mg.left, y); ctx.lineTo(mg.left + pW, y); ctx.stroke();
  });

  bars.forEach((b, i) => {
    const norm = b.score / maxScore;
    const bh   = norm * pH;
    const x    = mg.left + i * barW + barW * 0.12;
    const bw   = barW * 0.76;
    const y    = mg.top + pH - bh;
    const isBest = b.ch === best;

    // green (clear) → red (congested)
    const t  = norm;
    const r  = Math.round(52  + (248 - 52)  * t);
    const g  = Math.round(211 + (113 - 211) * t);
    const bl = Math.round(153 + (113 - 153) * t);
    ctx.fillStyle = isBest ? "#5ee1c8" : `rgb(${r},${g},${bl})`;
    if (bh > 0) ctx.fillRect(x, y, bw, bh);

    // highlight the recommended channel slot
    if (isBest) {
      ctx.strokeStyle = "#5ee1c8"; ctx.lineWidth = 1.5;
      ctx.strokeRect(mg.left + i * barW + 1, mg.top, barW - 2, pH);
    }

    const showLabel = band === "2.4" || bars.length <= 14 || i % 2 === 0;
    if (showLabel) {
      ctx.fillStyle = isBest ? "#5ee1c8" : textClr;
      ctx.font      = isBest ? "bold 9px ui-monospace,monospace" : "9px ui-monospace,monospace";
      ctx.textAlign = "center";
      ctx.fillText(String(b.ch), x + bw / 2, mg.top + pH + 14);
    }
  });

  const recoEl = document.getElementById(recoElId);
  if (recoEl) recoEl.textContent = bars.length ? `Recommended: channel ${best}` : "no data";
}

function _fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth  || 600;
  const H = canvas.offsetHeight || 220;
  // Back the canvas at device resolution, then draw in CSS pixels — crisp on Retina.
  canvas.width  = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { W, H, ctx };
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

    // Mark the user's own network
    if (MY_BSSID && (net.bssid || "").toUpperCase() === MY_BSSID) {
      ctx.fillStyle = "#fbbf24";
      ctx.font = "bold 14px ui-monospace,monospace";
      ctx.textAlign = "center";
      ctx.fillText("★", cx, cy - 7);
    }

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

  // Center label — sized + vertically centred to sit inside the hole
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = textClr;
  ctx.font = `bold ${Math.round(outerR * 0.40)}px ui-monospace,monospace`;
  ctx.fillText(String(total), cx, cy - outerR * 0.10);
  ctx.font = `${Math.round(outerR * 0.15)}px ui-monospace,monospace`;
  ctx.fillStyle = textClr + "99";
  ctx.fillText("networks", cx, cy + outerR * 0.22);
  ctx.textBaseline = "alphabetic";

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
      await showRssiModal(bssid, ssid, btn.dataset);
    });
  });
}

async function showRssiModal(bssid, ssid, meta = {}) {
  let modal = document.getElementById("rmRssiModal");
  if (modal) modal.remove();

  const chips = [];
  if (meta.channel)  chips.push(`ch ${esc(meta.channel)}`);
  if (meta.rssi)     chips.push(`${esc(meta.rssi)} dBm`);
  if (meta.security) chips.push(esc(meta.security));
  chips.push(`<span class="rm-mono">${esc(bssid)}</span>`);
  const statLine = `<div class="rm-rssi-stats">${chips.map(c => `<span>${c}</span>`).join("")}</div>`;

  modal = document.createElement("div");
  modal.id        = "rmRssiModal";
  modal.className = "rm-rssi-modal";
  modal.innerHTML = `
    <div class="rm-rssi-modal-inner">
      <div class="rm-rssi-modal-header">
        <h3>${esc(ssid)}</h3>
        <button class="rm-rssi-close" id="rmRssiCloseBtn">✕</button>
      </div>
      ${statLine}
      <canvas id="rmRssiHistCanvas" style="width:100%;height:200px;display:block"></canvas>
      <div class="rm-muted" id="rmRssiHistNote" style="font-size:0.72rem;margin-top:0.5rem;text-align:center">
        Loading…
      </div>
    </div>`;
  // Append inside the themed root so CSS variables (surface/line/teal) resolve.
  (document.getElementById("rmRoot") || document.body).appendChild(modal);
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

function aiHardwareLine(s) {
  const hw = s.hardware;
  if (!hw) return "";
  const ram = hw.ram_mb ? `${(hw.ram_mb / 1024).toFixed(hw.ram_mb >= 4096 ? 0 : 1)} GB` : "?";
  const board = (hw.board && hw.board !== "unknown") ? esc(hw.board) : "this host";
  const onHailo = s.backend === "hailo";
  // Accelerator chip: green when the NPU is the active engine.
  const accel = `<span class="rm-pw-pill" style="color:${onHailo ? "var(--rm-green)" : "var(--aap-muted)"}">
      ${onHailo ? "⚡ Hailo-10H NPU" : "CPU (llama.cpp)"}</span>`;
  const params = onHailo
    ? `<span class="rm-mono">model ${esc(s.model || "?")} · ${s.timeout}s</span>`
    : (s.threads ? `<span class="rm-mono">⚙ ${s.threads} threads · ctx ${s.ctx_size} · ${s.n_predict} tok · ${s.timeout}s</span>` : "");
  // If a Hailo-10H is present but we're NOT using it, hint that it's available.
  const hailoHint = (!onHailo && hw.hailo && hw.hailo.llm_capable)
    ? `<span class="rm-pw-pill" title="${esc(hw.hailo.note || "")}" style="color:var(--rm-amber)">AI HAT+ 2 present — set [ai] backend=hailo to use it</span>`
    : "";
  return `
    <div class="rm-muted" style="font-size:0.78rem;display:flex;flex-wrap:wrap;gap:0.4rem 0.9rem;margin:-0.3rem 0 0.2rem">
      <span>🖥 ${board} · ${hw.cores || "?"} cores · ${ram} RAM</span>
      ${accel}
      ${params}
      ${hailoHint}
    </div>`;
}

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
          <div class="rm-muted" style="font-size:0.8rem">IBM Granite — runs locally on the CPU</div>
        </div>
        <div class="rm-ai-status-row">
          <span class="rm-ai-status-dot ${statusClass}"></span>
          <span class="rm-muted" style="font-size:0.8rem">${statusLabel}</span>
          ${ready ? `<span class="rm-mono rm-muted" style="font-size:0.75rem">${esc(model)} · ${mb}MB</span>` : ""}
        </div>
      </div>

      ${aiHardwareLine(aiStatus)}
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
    const result = await post("/api/ai/chat", { messages }, AI_TIMEOUT_MS);
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
    appendAIBubble("assistant", e.name === "AbortError"
      ? "Timed out after 5 minutes — the Pi may be busy. Try again with scanning/cracking paused."
      : "Network error — is radioman running?");
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
        const result = await post("/api/ai/analyze", { type }, AI_TIMEOUT_MS);
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
        appendAIBubble("assistant", e.name === "AbortError"
          ? "Timed out after 5 minutes — the Pi may be busy. Try again with scanning/cracking paused."
          : "Request failed.");
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

// ── Settings view ─────────────────────────────────────────────────────────────
function viewSettings(settings, wifi, nets) {
  const theme = getTheme();
  const ssids = [...new Set((nets || []).map(n => n.ssid).filter(Boolean))].sort();
  const myBssid = (settings.my_bssid || "").toUpperCase();
  const apOptions = [...(nets || [])]
    .filter(n => n.bssid)
    .sort((a, b) => (b.rssi ?? -100) - (a.rssi ?? -100))
    .map(n => {
      const sel = (n.bssid || "").toUpperCase() === myBssid ? "selected" : "";
      return `<option value="${esc(n.bssid)}" data-ssid="${esc(n.ssid || "")}" ${sel}>${esc((n.ssid || "(hidden)") + " — " + n.bssid)}</option>`;
    }).join("");
  // Keep a saved "my network" selectable even if it isn't in the current scan.
  const haveMine = (nets || []).some(n => (n.bssid || "").toUpperCase() === myBssid);
  const savedOpt = (myBssid && !haveMine)
    ? `<option value="${esc(settings.my_bssid)}" data-ssid="${esc(settings.my_ssid || "")}" selected>${esc((settings.my_ssid || "(saved)") + " — " + settings.my_bssid)}</option>`
    : "";

  let wifiStatus;
  if (wifi.connecting) {
    wifiStatus = `<div class="rm-wifi-status"><span class="rm-spinner-sm"></span> ${esc(wifi.message || "Connecting…")}</div>`;
  } else if (wifi.connected) {
    wifiStatus = `<div class="rm-wifi-status rm-wifi-ok">
      <strong>${esc(wifi.ssid)}</strong>
      <span class="rm-muted">${wifi.signal != null ? wifi.signal + " dBm · " : ""}${esc(wifi.ip || "no IP")} · ${esc(wifi.manager || "")}</span>
    </div>`;
  } else {
    wifiStatus = `<div class="rm-wifi-status rm-wifi-off">Not connected to WiFi</div>`;
  }

  return `
    <div class="rm-settings">
      <div class="dash-panel dash-panel-full">
        <div class="dash-panel-header"><h3>Appearance</h3></div>
        <div class="rm-settings-body">
          <div class="rm-setting-row">
            <div>
              <div class="rm-setting-label">Theme</div>
              <div class="rm-muted" style="font-size:0.78rem">Dashboard color scheme</div>
            </div>
            <button class="rm-btn" id="rmThemeToggle">${theme === "dark" ? "Dark ☾" : "Light ☀︎"}</button>
          </div>
        </div>
      </div>

      <div class="dash-panel dash-panel-full">
        <div class="dash-panel-header"><h3>WiFi Connection</h3>
          <span class="rm-muted" style="font-size:0.72rem">join a network so the Pi stays online</span>
        </div>
        <div class="rm-settings-body">
          ${wifiStatus}
          <div class="rm-setting-form">
            <input class="rm-ignore-input" id="rmWifiSsid" list="rmSsidList" placeholder="Network name (SSID)"
                   maxlength="64" autocomplete="off" value="${esc(wifi.connected ? wifi.ssid : "")}" />
            <datalist id="rmSsidList">${ssids.map(s => `<option value="${esc(s)}"></option>`).join("")}</datalist>
            <input class="rm-ignore-input" id="rmWifiPass" type="password" placeholder="Password (blank if open)"
                   maxlength="64" autocomplete="off" />
            <button class="rm-btn rm-btn-primary" id="rmWifiConnectBtn" ${wifi.connecting ? "disabled" : ""}>Connect</button>
          </div>
          <div class="rm-muted" id="rmWifiMsg" style="font-size:0.78rem;margin-top:0.4rem">${esc(wifi.connecting ? "" : (wifi.message || ""))}</div>
          <div class="rm-muted" style="font-size:0.72rem;margin-top:0.5rem">
            ⚠ Switching WiFi can briefly drop wlan0 — manage the Pi over USB (10.55.0.1) when changing networks.
          </div>
        </div>
      </div>

      <div class="dash-panel dash-panel-full">
        <div class="dash-panel-header"><h3>Scan Settings</h3></div>
        <div class="rm-settings-body">
          <div class="rm-setting-stack">
            <label class="rm-setting-label" for="rmScanTarget">LAN scan target</label>
            <input class="rm-ignore-input rm-mono" id="rmScanTarget"
                   placeholder="auto-detect (e.g. 192.168.1.0/24)" maxlength="32" value="${esc(settings.scan_target || "")}" />
            <div class="rm-muted" style="font-size:0.72rem">Subnet the nmap LAN scan targets. Blank = auto-detect from ${esc(settings.iface || "wlan0")}.</div>
          </div>
          <div class="rm-setting-stack" style="margin-top:1rem">
            <label class="rm-setting-label" for="rmMyBssid">My network</label>
            <select class="rm-ignore-input" id="rmMyBssid">
              <option value="" ${myBssid ? "" : "selected"}>— none —</option>
              ${savedOpt}${apOptions}
            </select>
            <div class="rm-muted" style="font-size:0.72rem">Highlighted as &ldquo;yours&rdquo; in the Networks &amp; channel views.</div>
          </div>

          <div class="rm-setting-label" style="margin-top:1.5rem">L3 / VLAN topology (SNMP)</div>
          <div class="rm-muted" style="font-size:0.72rem;margin-bottom:0.5rem;max-width:520px">
            Optional. A read-only SNMP community lets the L3 Topology view map subnets, VLANs and
            cross-VLAN ARP from a managed gateway / switch. Leave blank for traceroute-only topology.
          </div>
          <div class="rm-setting-form">
            <input class="rm-ignore-input" id="rmSnmpCommunity" type="password" placeholder="Community (e.g. public)"
                   maxlength="64" autocomplete="off" value="${esc(settings.snmp_community || "")}" />
            <input class="rm-ignore-input rm-mono" id="rmSnmpTarget" placeholder="Target IP (blank = gateway)"
                   maxlength="64" autocomplete="off" value="${esc(settings.snmp_target || "")}" />
            <select class="rm-ignore-input" id="rmSnmpVersion" style="flex:0 0 auto">
              <option value="2c" ${(settings.snmp_version || "2c") === "2c" ? "selected" : ""}>v2c</option>
              <option value="1"  ${settings.snmp_version === "1" ? "selected" : ""}>v1</option>
            </select>
          </div>

          <div style="margin-top:1.25rem">
            <button class="rm-btn rm-btn-primary" id="rmSettingsSaveBtn">Save settings</button>
            <span id="rmSettingsMsg" class="rm-muted" style="margin-left:0.75rem;font-size:0.78rem"></span>
          </div>
        </div>
      </div>
    </div>`;
}

function attachSettingsHandlers() {
  document.getElementById("rmThemeToggle")?.addEventListener("click", () => {
    setTheme(getTheme() === "dark" ? "light" : "dark");
  });

  document.getElementById("rmWifiConnectBtn")?.addEventListener("click", async () => {
    const ssid = document.getElementById("rmWifiSsid")?.value.trim();
    const password = document.getElementById("rmWifiPass")?.value || "";
    const msg = document.getElementById("rmWifiMsg");
    if (!ssid) { if (msg) msg.textContent = "Enter a network name (SSID)."; return; }
    const btn = document.getElementById("rmWifiConnectBtn");
    btn.disabled = true; btn.textContent = "Connecting…";
    if (msg) msg.textContent = `Connecting to ${ssid}…`;
    try {
      const r = await post("/api/wifi/connect", { ssid, password });
      if (r.error) {
        if (msg) msg.textContent = r.error;
        btn.disabled = false; btn.textContent = "Connect";
      } else {
        setTimeout(poll, 1500);   // background connect; status updates on poll
      }
    } catch (e) {
      if (msg) msg.textContent = "Connection request failed.";
      btn.disabled = false; btn.textContent = "Connect";
    }
  });

  document.getElementById("rmSettingsSaveBtn")?.addEventListener("click", async () => {
    const scan_target = document.getElementById("rmScanTarget")?.value.trim() || "";
    const sel = document.getElementById("rmMyBssid");
    const my_bssid = sel?.value || "";
    const my_ssid  = sel?.selectedOptions?.[0]?.dataset?.ssid || "";
    const snmp_community = document.getElementById("rmSnmpCommunity")?.value || "";
    const snmp_target    = document.getElementById("rmSnmpTarget")?.value.trim() || "";
    const snmp_version   = document.getElementById("rmSnmpVersion")?.value || "2c";
    const msg = document.getElementById("rmSettingsMsg");
    try {
      const r = await post("/api/settings", {
        scan_target, my_bssid, my_ssid, snmp_community, snmp_target, snmp_version });
      if (msg) {
        msg.textContent = r.error || "Saved ✓";
        msg.style.color = r.error ? "var(--rm-red)" : "";
      }
    } catch (e) {
      if (msg) msg.textContent = "Save failed";
    }
  });
}

// ── Kick off ──────────────────────────────────────────────────────────────────
poll();
pollTimer = setInterval(poll, POLL_MS);
