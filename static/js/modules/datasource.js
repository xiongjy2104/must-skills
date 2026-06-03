// Data source: Excel/CSV upload, SQL DB, Google Sheets, Custom REST API + disconnect.
(function () {
  const { $ } = window.BAA.dom;
  const { closeOverlay, toast } = window.BAA.overlay;
  const state = window.BAA.state;

  function setSrc(name, hintKey, connected) {
    state.srcConnected = connected;
    state.srcName      = connected ? (name || "") : "";
    state.srcHintKey   = connected ? hintKey : 'sidebar.hint.noconn';

    // Invalidate preview cache whenever data source changes.
    if (window.BAA.preview) window.BAA.preview.invalidate();

    // Status dot — toggle the modifier class (works for both old .source-dot
    // and new .sb-status-dot selectors thanks to the alias in layout.css).
    const dot = $("src-dot");
    if (dot) dot.classList.toggle("on", connected);
    $("src-name").textContent = connected ? name : t('sidebar.disconnected');
    const hint = $("src-hint");
    if (hint) hint.textContent = t(hintKey);

    // Disconnect button — lives inside the dropdown menu now.
    const disc = $("btn-disc");
    if (disc) {
      disc.hidden = !connected;
      const sep = $("sb-disc-sep");
      if (sep) sep.hidden = !connected;
    }

    $("btn-schema").style.display = connected ? "" : "none";
    $("hdr-sub").textContent      = connected ? t('connected_to', { name }) : t('header.subtitle');

    // Sidebar gets .has-source — used to dim the "Add data source" CTA pulse
    // when the user already connected something.
    document.querySelector(".sidebar")?.classList.toggle("has-source", connected);

    if (connected) window.BAA.dom.hideWelcome();
  }

  function _showDsStatus(elId, name) {
    const el = $(elId);
    if (el) { el.textContent = t('ds.configured', { name }); el.style.display = ""; }
  }

  async function loadDatasourceConfigs() {
    let cfgs;
    try {
      const r = await fetch("/api/datasource-configs");
      cfgs = await r.json();
    } catch { return; }

    const sql = cfgs.sql || {};
    if (sql.has_connection_string) {
      $("db-conn").placeholder        = t('ds.conn_saved_ph');
      $("db-conn").dataset.hasSaved   = "1";
      if (sql.name) $("db-name").value = sql.name;
      _showDsStatus("db-status", sql.name || "SQL DB");
    }

    const gs = cfgs.gsheets || {};
    if (gs.has_creds_json) {
      $("gsheets-creds").placeholder      = t('ds.conn_saved_ph');
      $("gsheets-creds").dataset.hasSaved = "1";
      if (gs.spreadsheet) $("gsheets-sheet").value = gs.spreadsheet;
      if (gs.name)         $("gsheets-name").value = gs.name;
      _showDsStatus("gsheets-status", gs.name || "Google Sheets");
    }

    const api = cfgs.api || {};
    if (api.url) {
      $("api-url").value = api.url;
      if (api.auth_type) $("api-auth-type").value = api.auth_type;
      if (api.auth_type && api.auth_type !== "none") {
        $("api-auth-row").style.display = "";
      }
      if (api.has_auth_value) {
        $("api-auth-value").placeholder      = t('ds.conn_saved_ph');
        $("api-auth-value").dataset.hasSaved = "1";
      }
      if (api.name) $("api-name").value = api.name;
      _showDsStatus("api-status", api.name || api.url);
    }

    // ── Alibaba Cloud / Lark sources ──────────────────────────────────────
    const mc = cfgs.maxcompute || {};
    if (mc.project) {
      if (mc.access_id) $("mc-access-id").value = mc.access_id;
      if (mc.project)   $("mc-project").value   = mc.project;
      if (mc.endpoint)  $("mc-endpoint").value  = mc.endpoint;
      if (mc.name)      $("mc-name").value       = mc.name;
      if (mc.has_access_key) $("mc-access-key").placeholder = t('ds.conn_saved_ph');
      _showDsStatus("mc-status", mc.name || mc.project);
    }

    const sdb = cfgs.selectdb || {};
    if (sdb.host) {
      $("sdb-host").value     = sdb.host || "";
      $("sdb-port").value     = sdb.port || 9030;
      $("sdb-user").value     = sdb.user || "";
      $("sdb-database").value = sdb.database || "";
      if (sdb.name) $("sdb-name").value = sdb.name;
      if (sdb.has_password) $("sdb-password").placeholder = t('ds.conn_saved_ph');
      _showDsStatus("sdb-status", sdb.name || sdb.database);
    }

    const oss = cfgs.oss || {};
    if (oss.bucket) {
      $("oss-endpoint").value = oss.endpoint || "";
      $("oss-bucket").value   = oss.bucket || "";
      $("oss-object").value   = oss.object_key || "";
      $("oss-ak-id").value    = oss.access_key_id || "";
      if (oss.name) $("oss-name").value = oss.name;
      if (oss.has_access_key_secret) $("oss-ak-secret").placeholder = t('ds.conn_saved_ph');
      _showDsStatus("oss-status", oss.name || oss.bucket);
    }

    const lark = cfgs.lark || {};
    if (lark.spreadsheet_token) {
      $("lark-server").value = lark.server_id || "";
      $("lark-token").value  = lark.spreadsheet_token || "";
      $("lark-tool").value   = lark.read_tool || "";
      $("lark-ranges").value = (lark.ranges || []).join(", ");
      if (lark.name) $("lark-name").value = lark.name;
      _showDsStatus("lark-status", lark.name || "Lark");
    }
  }

  async function disconnectSrc() {
    await fetch(`/api/session/${state.SID}/datasource`, { method: "DELETE" });
    state.schemaText = "";
    setSrc(null, 'sidebar.hint.noconn', false);
    toast(t('toast.disconnected'));
  }

  function onXlFile() {
    const f = $("xl-file").files[0];
    $("xl-btn").disabled        = !f;
    $("xl-err").textContent     = "";
    $("xl-schema").style.display = "none";
  }

  async function uploadXl() {
    const f = $("xl-file").files[0];
    if (!f) return;
    const btn           = $("xl-btn");
    const cancelBtn     = $("xl-cancel-btn");
    const progressWrap  = $("xl-progress");
    const progressBar   = $("xl-progress-bar");
    const progressLabel = $("xl-progress-label");
    const errEl         = $("xl-err");

    btn.disabled       = true;
    cancelBtn.disabled = true;
    errEl.textContent  = "";
    progressWrap.style.display = "";
    progressBar.style.width    = "0%";

    const form = new FormData();
    form.append("file", f);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/session/${state.SID}/upload`);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round(e.loaded / e.total * 100);
        progressBar.style.width = pct + "%";
        progressBar.classList.remove("indeterminate");
        progressLabel.textContent = `${t('btn.uploading')} ${pct}%`;
      } else {
        progressBar.classList.add("indeterminate");
      }
    };

    xhr.upload.onloadend = () => {
      progressWrap.style.display = "none";
      progressBar.classList.remove("indeterminate");
      $("xl-parsing").style.display = "";
    };

    const d = await new Promise((resolve, reject) => {
      xhr.onload  = () => { try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error("服务器响应异常")); } };
      xhr.onerror = () => reject(new Error("网络错误"));
      xhr.send(form);
    }).catch(err => ({ error: err.message }));

    progressWrap.style.display = "none";
    progressBar.classList.remove("indeterminate");
    $("xl-parsing").style.display = "none";
    btn.disabled       = false;
    cancelBtn.disabled = false;

    if (d.error) { errEl.textContent = d.error; return; }
    state.schemaText = d.schema_preview || "";
    $("xl-schema").textContent  = state.schemaText;
    $("xl-schema").style.display = "block";
    setSrc(d.source_name, 'src.hint.file', true);
    closeOverlay("ov-excel");
    toast(t('toast.upload_ok'), "ok");
    window.sysMsg(t('sys.connected', { name: d.source_name }));
  }

  async function connectDB() {
    const conn = $("db-conn").value.trim();
    const name = $("db-name").value.trim();
    const hasSaved = $("db-conn").dataset.hasSaved === "1";
    if (!conn && !hasSaved) { $("db-err").textContent = t('conn_err'); return; }
    $("db-err").textContent = "";
    const loadingEl = $("db-loading");
    const btn       = $("db-btn");
    const cancelBtn = $("db-cancel-btn");
    loadingEl.style.display = "";
    btn.disabled       = true;
    cancelBtn.disabled = true;
    const r = await fetch(`/api/session/${state.SID}/connect-db`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ connection_string: conn, name }),
    });
    const d = await r.json();
    loadingEl.style.display = "none";
    btn.disabled       = false;
    cancelBtn.disabled = false;
    if (d.error) { $("db-err").textContent = d.error; return; }
    state.schemaText = d.schema_preview || "";
    $("db-schema").textContent  = state.schemaText;
    $("db-schema").style.display = "block";
    setSrc(d.source_name, 'src.hint.db', true);
    closeOverlay("ov-db");
    toast(t('toast.db_ok'), "ok");
    window.sysMsg(t('sys.connected', { name: d.source_name }));
  }

  async function connectGSheets() {
    const creds = $("gsheets-creds").value.trim();
    const sheet = $("gsheets-sheet").value.trim();
    const name  = $("gsheets-name").value.trim();
    const errEl = $("gsheets-err");
    const hasSavedCreds = $("gsheets-creds").dataset.hasSaved === "1";
    if (!creds && !hasSavedCreds) { errEl.textContent = t('gsheets_err.no_creds'); return; }
    if (!sheet)                   { errEl.textContent = t('gsheets_err.no_sheet'); return; }
    errEl.textContent = "";
    const loadingEl = $("gsheets-loading");
    const btn       = $("gsheets-btn");
    const cancelBtn = $("gsheets-cancel-btn");
    loadingEl.style.display = "";
    btn.disabled       = true;
    cancelBtn.disabled = true;
    const r = await fetch(`/api/session/${state.SID}/connect-gsheets`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ creds_json: creds, spreadsheet: sheet, name }),
    });
    const d = await r.json();
    loadingEl.style.display = "none";
    btn.disabled       = false;
    cancelBtn.disabled = false;
    if (d.error) { errEl.textContent = d.error; return; }
    state.schemaText = d.schema_preview || "";
    $("gsheets-schema").textContent  = state.schemaText;
    $("gsheets-schema").style.display = "block";
    setSrc(d.source_name, 'src.hint.gsheets', true);
    closeOverlay("ov-gsheets");
    toast(t('toast.gsheets_ok'), "ok");
    window.sysMsg(t('sys.connected', { name: d.source_name }));
  }

  function toggleApiAuthValue() {
    const type = $("api-auth-type").value;
    $("api-auth-row").style.display = type === "none" ? "none" : "";
  }

  async function connectAPI() {
    const url       = $("api-url").value.trim();
    const authType  = $("api-auth-type").value;
    const authValue = $("api-auth-value").value.trim();
    const name      = $("api-name").value.trim();
    const errEl     = $("api-err");
    if (!url) { errEl.textContent = t('api_err.no_url'); return; }
    errEl.textContent = "";
    const loadingEl = $("api-loading");
    const btn       = $("api-btn");
    const cancelBtn = $("api-cancel-btn");
    loadingEl.style.display = "";
    btn.disabled       = true;
    cancelBtn.disabled = true;
    const r = await fetch(`/api/session/${state.SID}/connect-api`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, auth_type: authType, auth_value: authValue, name }),
    });
    const d = await r.json();
    loadingEl.style.display = "none";
    btn.disabled       = false;
    cancelBtn.disabled = false;
    if (d.error) { errEl.textContent = d.error; return; }
    state.schemaText = d.schema_preview || "";
    $("api-schema").textContent  = state.schemaText;
    $("api-schema").style.display = "block";
    setSrc(d.source_name, 'src.hint.api', true);
    closeOverlay("ov-api");
    toast(t('toast.api_ok'), "ok");
    window.sysMsg(t('sys.connected', { name: d.source_name }));
  }

  // ── Alibaba Cloud / Lark custom sources ─────────────────────────────────
  // Generic connect: POST `payload` to `endpoint`, then update UI on success.
  // `ui` carries the per-source DOM id prefix, overlay id, hint key and toast.
  async function _connect(endpoint, payload, ui) {
    const errEl = $(ui.prefix + "-err");
    if (errEl) errEl.textContent = "";
    const loadingEl = $(ui.prefix + "-loading");
    const btn       = $(ui.prefix + "-btn");
    if (loadingEl) loadingEl.style.display = "";
    if (btn) btn.disabled = true;

    let d;
    try {
      const r = await fetch(`/api/session/${state.SID}/${endpoint}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      d = await r.json();
    } catch (e) {
      d = { error: (e && e.message) || "网络错误" };
    }

    if (loadingEl) loadingEl.style.display = "none";
    if (btn) btn.disabled = false;
    if (d.error) { if (errEl) errEl.textContent = d.error; return; }

    state.schemaText = d.schema_preview || "";
    const schemaEl = $(ui.prefix + "-schema");
    if (schemaEl) { schemaEl.textContent = state.schemaText; schemaEl.style.display = "block"; }
    setSrc(d.source_name, ui.hintKey, true);
    closeOverlay(ui.overlay);
    toast(t(ui.toastKey), "ok");
    window.sysMsg(t('sys.connected', { name: d.source_name }));
  }

  async function connectMaxCompute() {
    await _connect("connect-maxcompute", {
      name:       $("mc-name").value.trim(),
      access_id:  $("mc-access-id").value.trim(),
      access_key: $("mc-access-key").value.trim(),
      project:    $("mc-project").value.trim(),
      endpoint:   $("mc-endpoint").value.trim(),
    }, { prefix: "mc", overlay: "ov-maxcompute", hintKey: "src.hint.db", toastKey: "toast.db_ok" });
  }

  async function connectSelectDB() {
    await _connect("connect-selectdb", {
      name:     $("sdb-name").value.trim(),
      host:     $("sdb-host").value.trim(),
      port:     $("sdb-port").value.trim(),
      user:     $("sdb-user").value.trim(),
      password: $("sdb-password").value.trim(),
      database: $("sdb-database").value.trim(),
    }, { prefix: "sdb", overlay: "ov-selectdb", hintKey: "src.hint.db", toastKey: "toast.db_ok" });
  }

  async function connectOSS() {
    await _connect("connect-oss", {
      name:              $("oss-name").value.trim(),
      endpoint:          $("oss-endpoint").value.trim(),
      bucket:            $("oss-bucket").value.trim(),
      object_key:        $("oss-object").value.trim(),
      access_key_id:     $("oss-ak-id").value.trim(),
      access_key_secret: $("oss-ak-secret").value.trim(),
    }, { prefix: "oss", overlay: "ov-oss", hintKey: "src.hint.file", toastKey: "toast.upload_ok" });
  }

  async function connectLark() {
    await _connect("connect-lark", {
      name:              $("lark-name").value.trim(),
      server_id:         $("lark-server").value.trim(),
      spreadsheet_token: $("lark-token").value.trim(),
      ranges:            $("lark-ranges").value.trim(),
      read_tool:         $("lark-tool").value.trim(),
    }, { prefix: "lark", overlay: "ov-lark", hintKey: "src.hint.gsheets", toastKey: "toast.gsheets_ok" });
  }

  window.BAA.datasource = {
    setSrc, loadDatasourceConfigs, disconnectSrc,
    onXlFile, uploadXl, connectDB, connectGSheets, connectAPI, toggleApiAuthValue,
    connectMaxCompute, connectSelectDB, connectOSS, connectLark,
  };

  // Backward-compat (used by sessions.js and language change handler).
  window.setSrc = setSrc;
})();
