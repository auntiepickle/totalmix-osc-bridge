/* api.js — centralized fetch layer
 * All HTTP calls go through window.API.*
 * Loaded before app.js and ui.js so both can use it immediately.
 *
 * Convention:
 *   - Every method returns the parsed JSON (or throws on non-2xx).
 *   - Callers handle errors with try/catch.
 *   - Raw Response is never exposed — only parsed data or thrown Error.
 */

window.API = (function () {

  // ── Internal helpers ──────────────────────────────────────────────────────

  async function _get(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`GET ${path} → HTTP ${res.status}`);
    return res.json();
  }

  async function _post(path, body) {
    const res = await fetch(path, {
      method: 'POST',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body:    body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `POST ${path} → HTTP ${res.status}`);
    }
    return res.json().catch(() => ({}));
  }

  async function _postForm(path, formData) {
    const res = await fetch(path, { method: 'POST', body: formData });
    if (!res.ok) throw new Error(`POST ${path} → HTTP ${res.status}`);
    return res.json().catch(() => ({}));
  }

  // ── Public API ────────────────────────────────────────────────────────────

  return {
    /** GET /api/macros → { name: macroObj, … } */
    getMacros() { return _get('/api/macros'); },

    /** GET /api/status → { workspace, snapshot, mappings_is_example, … } */
    getStatus() { return _get('/api/status'); },

    /** GET /api/health → { mqtt_connected, osc_configured } */
    getHealth() { return _get('/api/health'); },

    /** GET /api/snapshot_map → { workspace: { snapshots: {…} }, … } */
    getSnapshotMap() { return _get('/api/snapshot_map'); },

    /** GET /api/config/:type → raw JSON string (text, not parsed) */
    async getConfig(type) {
      const res = await fetch(`/api/config/${type}`);
      if (!res.ok) throw new Error(`GET /api/config/${type} → HTTP ${res.status}`);
      return res.text();
    },

    /** POST /api/config/:type  body: raw JSON string */
    async saveConfig(type, jsonText) {
      const res = await fetch(`/api/config/${type}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    jsonText,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `Save ${type} → HTTP ${res.status}`);
      }
      return res.json().catch(() => ({}));
    },

    /** POST /api/config/mappings/init-from-example */
    initMappingsFromExample() {
      return _post('/api/config/mappings/init-from-example');
    },

    /** POST /api/config/macros/:name — save a single macro */
    saveMacro(name, data) {
      return _post(`/api/config/macros/${encodeURIComponent(name)}`, data);
    },

    /** POST /api/trigger/:name  { param, clock_bpm } */
    trigger(name, param = 1.0, clockBpm = null) {
      return _post(`/api/trigger/${encodeURIComponent(name)}`, {
        param,
        clock_bpm: clockBpm,
      });
    },

    /** POST /api/switch  { workspace, snapshot? } */
    switch(workspace, snapshot = null) {
      return _post('/api/switch', { workspace, snapshot });
    },

    /** POST /api/reload */
    reload() { return _post('/api/reload'); },

    /** POST /api/upload/:type  FormData */
    upload(type, formData) {
      return _postForm(`/api/upload/${type}`, formData);
    },
  };

})();
