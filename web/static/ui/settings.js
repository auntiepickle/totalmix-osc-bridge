/* ui/settings.js — settings menu, server reload, file upload, live config editor.
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

// ── Settings menu ─────────────────────────────────────────────────────────────
async function toggleSettingsMenu() {
  const menu = document.getElementById('settings-menu');
  if (!menu) return;
  menu.classList.toggle('hidden');
  if (!menu.classList.contains('hidden')) {
    try {
      const s = await API.getStatus();
      const info = document.getElementById('settings-status');
      if (info) {
        const wsCount = s.snapshot_map_workspaces || 0;
        const wsText = wsCount > 0
          ? `<span class="text-zinc-400">${wsCount} workspace${wsCount !== 1 ? 's' : ''}</span>`
          : `<span class="text-red-400" title="ufx2_snapshot_map.json not loaded">⚠ no snapshot map</span>`;
        const submixLabel = s.channel_map_submixes > 0 ? s.channel_map_submixes : 0;
        const submixEx = s.channel_map_is_example
          ? `<span class="text-amber-400/80" title="Using ufx2_channel_map.example.json — routing labels may not match your setup">${submixLabel} submix${submixLabel !== 1 ? 'es' : ''} (example)</span>`
          : `<span class="text-zinc-400">${submixLabel} submix${submixLabel !== 1 ? 'es' : ''}</span>`;
        info.innerHTML =
          `<span class="text-zinc-400">${s.macros} macro${s.macros !== 1 ? 's' : ''}</span>` +
          ` · ${submixEx}` +
          ` · ${wsText}`;
      }
    } catch (_) {}
  }
}

document.addEventListener('click', (e) => {
  const menu = document.getElementById('settings-menu');
  if (!menu || menu.classList.contains('hidden')) return;
  if (!menu.contains(e.target) && !e.target.closest('[data-settings-toggle]')) {
    menu.classList.add('hidden');
  }
});

// ── Server reload ─────────────────────────────────────────────────────────────
async function reloadServer() {
  if (confirm('Reload bridge server?')) {
    await API.reload();
    location.reload();
  }
}

// ── File upload (legacy — kept for drag-and-drop workflows) ──────────────────
function uploadFile(input, type) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  API.upload(type, formData)
    .then(() => location.reload())
    .catch(e => alert(`Upload failed: ${e.message}`))   // 400 detail / 413 too large
    .finally(() => { input.value = ''; });   // so re-picking the same file fires onchange
}

// ── Live Config Editor ────────────────────────────────────────────────────────
async function openEditor(configType = 'mappings') {
  const modal    = document.getElementById('editor-modal');
  const textarea = document.getElementById('editor-textarea');
  const statusEl = document.getElementById('editor-status');
  if (!modal || !textarea) return;

  // Tab styling
  ['mappings', 'channel_map', 'snapshot_map'].forEach(t => {
    const tab = document.getElementById(`editor-tab-${t}`);
    if (!tab) return;
    tab.className = t === configType
      ? 'text-xs px-3 py-1.5 rounded-lg bg-orange-500 text-black font-bold transition-colors'
      : 'text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-400 hover:text-white transition-colors';
  });

  modal.dataset.configType = configType;
  if (statusEl) statusEl.textContent = 'Loading…';
  modal.classList.remove('hidden');

  try {
    const text = await API.getConfig(configType);
    // getConfig returns raw text; pretty-print it
    textarea.value = JSON.stringify(JSON.parse(text), null, 2);
    if (statusEl) statusEl.textContent = '';
    textarea.focus();
  } catch (e) {
    console.error('[UI] openEditor error:', e);
    textarea.value = '// Error loading config';
    if (statusEl) statusEl.textContent = 'Error';
  }
}

async function saveEditor() {
  const modal    = document.getElementById('editor-modal');
  const textarea = document.getElementById('editor-textarea');
  const statusEl = document.getElementById('editor-status');
  if (!modal || !textarea) return;

  const configType = modal.dataset.configType;
  let data;
  try {
    data = JSON.parse(textarea.value);
  } catch (e) {
    alert(`Invalid JSON:\n${e.message}`);
    return;
  }

  if (statusEl) statusEl.textContent = 'Saving…';
  try {
    await API.saveConfig(configType, textarea.value);
    modal.classList.add('hidden');
    if (configType === 'snapshot_map') {
      // Refresh local snapshot map cache so detail panels show correct validation
      window._snapshotMap = await API.getSnapshotMap().catch(() => ({}));
    } else {
      // Hot-reload macro cards from updated bridge mappings
      await loadMacros();
      renderCards();
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Error';
    alert(`Save error: ${e.message}`);
  }
}

function closeEditor() {
  const modal = document.getElementById('editor-modal');
  if (modal) modal.classList.add('hidden');
}

function formatEditorJSON() {
  const textarea = document.getElementById('editor-textarea');
  if (!textarea) return;
  try {
    textarea.value = JSON.stringify(JSON.parse(textarea.value), null, 2);
  } catch (e) {
    alert(`Invalid JSON: ${e.message}`);
  }
}

// Close modals on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeEditor(); closeNewMacro(); }
});


