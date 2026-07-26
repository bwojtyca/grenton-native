// Grenton Native — live communication monitor panel (vanilla, no build step).
// HA injects `hass` on this custom element. Everything is driven from here:
// upload the .omp (drag & drop), watch CLU liveness and the live native wire log.

const MAX_ROWS = 400;

const KIND_COLOR = {
  request: "#3b82f6",
  response: "#10b981",
  report: "#8b5cf6",
  error: "#ef4444",
  status: "#a1a1aa",
};

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

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
        .gn { padding:16px; color:var(--primary-text-color);
              font-family:var(--paper-font-body1_-_font-family, sans-serif); }
        .gn h1 { font-size:20px; margin:0 0 4px; }
        .gn .sub { color:var(--secondary-text-color); font-size:13px; margin-bottom:12px; }
        .drop { border:2px dashed var(--divider-color,#9993); border-radius:14px;
                padding:22px; text-align:center; color:var(--secondary-text-color);
                cursor:pointer; transition:.15s; margin-bottom:16px; }
        .drop.hot { border-color:var(--primary-color,#03a9f4);
                    background:color-mix(in srgb, var(--primary-color,#03a9f4) 10%, transparent); }
        .drop strong { color:var(--primary-text-color); }
        .clus { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px; }
        .card { background:var(--card-background-color,#fff);
                border:1px solid var(--divider-color,#e0e0e0); border-radius:12px;
                padding:12px 14px; min-width:220px; }
        .card .name { font-weight:600; }
        .card .ip { color:var(--secondary-text-color); font-size:12px; }
        .badge { display:inline-block; padding:2px 8px; border-radius:999px;
                 font-size:12px; color:#fff; margin-top:6px; }
        .btn { background:var(--primary-color,#03a9f4); color:#fff; border:none;
               border-radius:8px; padding:5px 10px; cursor:pointer; font-size:12px; }
        .controls { display:flex; gap:10px; align-items:center; margin:8px 0; flex-wrap:wrap; }
        table.log { width:100%; border-collapse:collapse; font-size:12px;
                    font-family:var(--code-font-family, monospace); }
        table.log th, table.log td { text-align:left; padding:4px 8px;
                    border-bottom:1px solid var(--divider-color,#eee); vertical-align:top; }
        table.log th { position:sticky; top:0; background:var(--card-background-color,#fff); }
        .kind { color:#fff; padding:1px 6px; border-radius:6px; font-size:11px; }
        .summary { word-break:break-all; }
        .logwrap { max-height:60vh; overflow:auto;
                   border:1px solid var(--divider-color,#e0e0e0); border-radius:12px; }
        .gn input, .gn select { background:var(--card-background-color,#fff);
                   color:var(--primary-text-color); border:1px solid var(--divider-color,#ccc);
                   border-radius:8px; padding:4px 8px; font-size:13px; }
        .watch { display:flex; gap:8px; align-items:center; flex-wrap:wrap;
                 margin:4px 0 12px; padding:10px 12px; border-radius:12px;
                 background:var(--card-background-color,#fff);
                 border:1px solid var(--divider-color,#e0e0e0); }
      </style>
      <div class="gn">
        <h1>Grenton Native — monitor komunikacji</h1>
        <div class="sub" id="status">Ładowanie…</div>

        <div class="drop" id="drop">
          <div><strong>Przeciągnij tutaj plik .omp</strong> (lub kliknij, aby wybrać)</div>
          <div style="font-size:12px; margin-top:4px">Projekt Object Managera dostarcza klucz AES i listę CLU.</div>
          <input type="file" id="file" accept=".omp" style="display:none">
        </div>

        <div class="clus" id="clus"></div>

        <div class="watch">
          <strong>Obserwuj obiekt:</strong>
          <select id="watch-clu"></select>
          <input id="watch-object" placeholder="obiekt z OM, np. DOU2341" style="min-width:200px">
          <span style="font-size:12px; color:var(--secondary-text-color)">indeksy</span>
          <input id="watch-indices" value="0" style="width:70px">
          <button class="btn" id="watch-btn">Obserwuj</button>
          <span id="watch-msg" style="font-size:12px; color:var(--secondary-text-color)"></span>
        </div>

        <div class="controls">
          <strong>Log komunikacji (na żywo)</strong>
          <label style="font-size:12px"><input type="checkbox" id="follow" checked> auto-scroll</label>
          <button class="btn" id="clear">Wyczyść</button>
        </div>
        <div class="logwrap">
          <table class="log">
            <thead><tr><th>czas</th><th>CLU</th><th>kier.</th><th>typ</th><th>msg_id</th><th>treść</th></tr></thead>
            <tbody id="log"></tbody>
          </table>
        </div>
      </div>`;

    const drop = this.querySelector("#drop");
    const file = this.querySelector("#file");
    drop.addEventListener("click", () => file.click());
    file.addEventListener("change", () => {
      if (file.files[0]) this._upload(file.files[0]);
    });
    ["dragenter", "dragover"].forEach((e) =>
      drop.addEventListener(e, (ev) => {
        ev.preventDefault();
        drop.classList.add("hot");
      })
    );
    ["dragleave", "drop"].forEach((e) =>
      drop.addEventListener(e, (ev) => {
        ev.preventDefault();
        drop.classList.remove("hot");
      })
    );
    drop.addEventListener("drop", (ev) => {
      const f = ev.dataTransfer?.files?.[0];
      if (f) this._upload(f);
    });
    this.querySelector("#clear").addEventListener("click", () => {
      this._rows = [];
      this.querySelector("#log").innerHTML = "";
    });
    this.querySelector("#watch-btn").addEventListener("click", () => this._watch());
  }

  async _watch() {
    const serial = this.querySelector("#watch-clu").value;
    const object = this.querySelector("#watch-object").value.trim();
    const indices = this.querySelector("#watch-indices").value.trim() || "0";
    const msg = this.querySelector("#watch-msg");
    if (!serial || !object) {
      msg.textContent = "podaj CLU i nazwę obiektu";
      return;
    }
    msg.textContent = "subskrybuję…";
    try {
      await this._hass.callWS({ type: "grenton_native/watch", serial, object, indices });
      msg.textContent = `obserwuję ${object} [${indices}] na ${serial} — zmień stan i patrz w log`;
    } catch (err) {
      msg.textContent = "błąd: " + (err.message || err);
    }
  }

  _setStatus(text) {
    const el = this.querySelector("#status");
    if (el) el.textContent = text;
  }

  async _load() {
    try {
      this._applySnapshot(await this._hass.callWS({ type: "grenton_native/status" }));
    } catch (err) {
      this._setStatus("Monitor nie jest gotowy: " + (err.message || err));
    }
  }

  async _subscribe() {
    try {
      this._unsub = this._hass.connection.subscribeMessage(
        (event) => this._addRow(event, true),
        { type: "grenton_native/subscribe" }
      );
    } catch (err) {
      /* status line already reflects readiness */
    }
  }

  async _upload(file) {
    this._setStatus("Wysyłanie " + file.name + " …");
    try {
      const b64 = await fileToBase64(file);
      const snap = await this._hass.callWS({
        type: "grenton_native/upload_omp",
        omp_base64: b64,
      });
      this._applySnapshot(snap);
    } catch (err) {
      this._setStatus("Błąd wgrywania: " + (err.message || err));
    }
  }

  _applySnapshot(snap) {
    if (!snap) return;
    this._renderClus(snap.clus || []);
    // rebuild the log from the snapshot
    this.querySelector("#log").innerHTML = "";
    this._rows = [];
    (snap.events || []).forEach((e) => this._addRow(e, false));
    if (snap.configured) {
      this._setStatus(`${(snap.clus || []).length} CLU · ${snap.last_seq || 0} zdarzeń`);
    } else {
      this._setStatus("Brak projektu — przeciągnij plik .omp powyżej, aby zacząć.");
    }
  }

  _renderClus(clus) {
    const el = this.querySelector("#clus");
    el.innerHTML = "";
    const sel = this.querySelector("#watch-clu");
    if (sel) {
      sel.innerHTML = "";
      clus.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.serial;
        opt.textContent = c.object_name || c.serial;
        sel.appendChild(opt);
      });
    }
    clus.forEach((c) => {
      const color = c.alive == null ? "#a1a1aa" : c.alive ? "#10b981" : "#ef4444";
      const label = c.alive == null ? "?" : c.alive ? "online" : "offline";
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="name">${escapeHtml(c.object_name || c.serial)}</div>
        <div class="ip">${escapeHtml(c.ip || "?")} · port ${c.report_port}</div>
        <div class="badge" style="background:${color}">${label}${
          c.reply ? " · " + escapeHtml(String(c.reply)) : ""
        }</div>
        <div style="margin-top:8px"><button class="btn" data-serial="${escapeHtml(c.serial)}">Ping</button></div>`;
      card.querySelector("button").addEventListener("click", async (ev) => {
        const serial = ev.target.getAttribute("data-serial");
        ev.target.disabled = true;
        try {
          await this._hass.callWS({ type: "grenton_native/check_alive", serial });
        } finally {
          ev.target.disabled = false;
        }
      });
      el.appendChild(card);
    });
  }

  _addRow(e, live) {
    const tbody = this.querySelector("#log");
    if (!tbody) return;
    const tr = document.createElement("tr");
    const t = new Date((e.ts || 0) * 1000).toLocaleTimeString();
    const kindColor = KIND_COLOR[e.kind] || "#666";
    const arrow = e.direction === "out" ? "→" : "←";
    tr.innerHTML = `
      <td>${t}</td>
      <td>${escapeHtml(e.clu ?? "")}</td>
      <td style="font-weight:600">${arrow}</td>
      <td><span class="kind" style="background:${kindColor}">${escapeHtml(e.kind)}</span></td>
      <td>${escapeHtml(e.msg_id ?? "")}</td>
      <td class="summary">${escapeHtml(e.summary ?? "")}${
        e.detail != null
          ? ' <em style="color:var(--secondary-text-color)">' +
            escapeHtml(JSON.stringify(e.detail)) +
            "</em>"
          : ""
      }</td>`;
    tbody.appendChild(tr);
    this._rows.push(tr);
    while (this._rows.length > MAX_ROWS) {
      const old = this._rows.shift();
      if (old && old.parentNode) old.parentNode.removeChild(old);
    }
    if (live && this.querySelector("#follow")?.checked) {
      const wrap = this.querySelector(".logwrap");
      if (wrap) wrap.scrollTop = wrap.scrollHeight;
    }
  }
}

customElements.define("grenton-native-panel", GrentonNativePanel);
