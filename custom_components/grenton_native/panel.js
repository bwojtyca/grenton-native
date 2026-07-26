// Grenton Native — live communication monitor panel (vanilla, no build step).
// HA injects `hass` on this custom element; we call our websocket commands
// (grenton_native/status, /subscribe, /check_alive) and render live.

const MAX_ROWS = 400;

const KIND_COLOR = {
  request: "#3b82f6",
  response: "#10b981",
  report: "#8b5cf6",
  error: "#ef4444",
  status: "#a1a1aa",
};

class GrentonNativePanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._init = false;
    this._unsub = null;
    this._rows = [];
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._init) {
      this._init = true;
      this._render();
      this._load();
      this._subscribe();
    }
  }

  disconnectedCallback() {
    if (this._unsub) {
      this._unsub.then((u) => u && u());
      this._unsub = null;
    }
  }

  _render() {
    this.innerHTML = `
      <style>
        .gn-wrap { padding: 16px; font-family: var(--paper-font-body1_-_font-family, sans-serif);
                   color: var(--primary-text-color); }
        .gn-h { display:flex; align-items:center; gap:12px; margin-bottom:12px; }
        .gn-h h1 { font-size: 20px; margin:0; }
        .gn-sub { color: var(--secondary-text-color); font-size:13px; }
        .gn-clus { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px; }
        .gn-card { background: var(--card-background-color, #fff);
                   border:1px solid var(--divider-color, #e0e0e0); border-radius:12px;
                   padding:12px 14px; min-width:220px; box-shadow: var(--ha-card-box-shadow, none); }
        .gn-card .name { font-weight:600; }
        .gn-card .ip { color: var(--secondary-text-color); font-size:12px; }
        .gn-badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px;
                    color:#fff; margin-top:6px; }
        .gn-row-actions { margin-top:8px; }
        button.gn-btn { background: var(--primary-color,#03a9f4); color:#fff; border:none;
                        border-radius:8px; padding:5px 10px; cursor:pointer; font-size:12px; }
        table.gn-log { width:100%; border-collapse:collapse; font-size:12px;
                       font-family: var(--code-font-family, monospace); }
        table.gn-log th, table.gn-log td { text-align:left; padding:4px 8px;
                       border-bottom:1px solid var(--divider-color,#eee); vertical-align:top; }
        table.gn-log th { position:sticky; top:0; background: var(--card-background-color,#fff); }
        .gn-dir { font-weight:600; }
        .gn-kind { color:#fff; padding:1px 6px; border-radius:6px; font-size:11px; }
        .gn-summary { word-break: break-all; }
        .gn-controls { display:flex; gap:8px; align-items:center; margin:8px 0; }
        .gn-log-wrap { max-height: 60vh; overflow:auto; border:1px solid var(--divider-color,#e0e0e0);
                       border-radius:12px; }
      </style>
      <div class="gn-wrap">
        <div class="gn-h">
          <h1>Grenton Native — monitor komunikacji</h1>
        </div>
        <div class="gn-sub" id="gn-status">Ładowanie…</div>
        <div class="gn-clus" id="gn-clus"></div>
        <div class="gn-controls">
          <strong>Log komunikacji (na żywo)</strong>
          <label style="font-size:12px"><input type="checkbox" id="gn-follow" checked> auto-scroll</label>
          <button class="gn-btn" id="gn-clear">Wyczyść</button>
        </div>
        <div class="gn-log-wrap">
          <table class="gn-log">
            <thead><tr><th>czas</th><th>CLU</th><th>kier.</th><th>typ</th><th>msg_id</th><th>treść</th></tr></thead>
            <tbody id="gn-log"></tbody>
          </table>
        </div>
      </div>`;
    this.querySelector("#gn-clear").addEventListener("click", () => {
      this._rows = [];
      this.querySelector("#gn-log").innerHTML = "";
    });
  }

  async _load() {
    try {
      const snap = await this._hass.callWS({ type: "grenton_native/status" });
      this._renderClus(snap.clus || []);
      (snap.events || []).forEach((e) => this._addRow(e, false));
      this.querySelector("#gn-status").textContent =
        `${(snap.clus || []).length} CLU · ${snap.last_seq || 0} zdarzeń w buforze`;
    } catch (err) {
      this.querySelector("#gn-status").textContent = "Monitor nie jest gotowy: " + err;
    }
  }

  async _subscribe() {
    try {
      this._unsub = this._hass.connection.subscribeMessage(
        (event) => this._addRow(event, true),
        { type: "grenton_native/subscribe" }
      );
    } catch (err) {
      /* ignore; status line already reports readiness */
    }
  }

  _renderClus(clus) {
    const el = this.querySelector("#gn-clus");
    el.innerHTML = "";
    clus.forEach((c) => {
      const alive = c.alive === true;
      const color = c.alive == null ? "#a1a1aa" : alive ? "#10b981" : "#ef4444";
      const label = c.alive == null ? "?" : alive ? "online" : "offline";
      const card = document.createElement("div");
      card.className = "gn-card";
      card.innerHTML = `
        <div class="name">${c.object_name || c.serial}</div>
        <div class="ip">${c.ip || "?"} · port ${c.report_port}</div>
        <div class="gn-badge" style="background:${color}">${label}${c.reply ? " · " + c.reply : ""}</div>
        <div class="gn-row-actions"><button class="gn-btn" data-serial="${c.serial}">Ping</button></div>`;
      card.querySelector("button").addEventListener("click", async (ev) => {
        const serial = ev.target.getAttribute("data-serial");
        ev.target.disabled = true;
        try { await this._hass.callWS({ type: "grenton_native/check_alive", serial }); }
        finally { ev.target.disabled = false; }
      });
      el.appendChild(card);
    });
  }

  _addRow(e, live) {
    const tbody = this.querySelector("#gn-log");
    if (!tbody) return;
    const tr = document.createElement("tr");
    const t = new Date((e.ts || 0) * 1000).toLocaleTimeString();
    const kindColor = KIND_COLOR[e.kind] || "#666";
    const arrow = e.direction === "out" ? "→" : "←";
    tr.innerHTML = `
      <td>${t}</td>
      <td>${e.clu ?? ""}</td>
      <td class="gn-dir">${arrow}</td>
      <td><span class="gn-kind" style="background:${kindColor}">${e.kind}</span></td>
      <td>${e.msg_id ?? ""}</td>
      <td class="gn-summary">${escapeHtml(e.summary ?? "")}${
        e.detail != null ? ' <em style="color:var(--secondary-text-color)">' + escapeHtml(JSON.stringify(e.detail)) + "</em>" : ""
      }</td>`;
    if (live) tbody.appendChild(tr);
    else tbody.appendChild(tr);
    this._rows.push(tr);
    while (this._rows.length > MAX_ROWS) {
      const old = this._rows.shift();
      if (old && old.parentNode) old.parentNode.removeChild(old);
    }
    if (live && this.querySelector("#gn-follow")?.checked) {
      const wrap = this.querySelector(".gn-log-wrap");
      if (wrap) wrap.scrollTop = wrap.scrollHeight;
    }
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

customElements.define("grenton-native-panel", GrentonNativePanel);
