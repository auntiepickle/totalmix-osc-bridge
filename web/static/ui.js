/* ui.js — card rendering, animation, fire/ramp, file upload, server reload */
/* Globals (macros, midiConnectedDevice, etc.) live in app.js — loaded first  */

function calculateDurationMs(macro, isRamp) {
  if (macro.durationMs) return macro.durationMs;
  const step = macro.steps ? macro.steps.find(s => s.operation) : null;
  if (!step || !step.operation) return isRamp ? 3500 : 2000;
  const op = step.operation;
  const bars = op.bars || 2;
  const bpm = op.bpm || 140;
  const msPerBar = 240000 / bpm;
  return Math.round(bars * msPerBar);
}

// Returns the first midi_trigger CC label for a macro, e.g. "CC42 • ch1"
function getMidiTriggerLabel(m) {
  const t = m.midi_triggers && m.midi_triggers[0];
  if (!t) return '';
  return `CC${t.number} • ch${t.channel}`;
}

function createMacroCardHTML(name, m) {
  const midiLabel = getMidiTriggerLabel(m);
  return `
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-5 rounded-2xl">
    <div class="flex justify-between items-start mb-3">
        <div class="flex-1 min-w-0 pr-3">
            <h3 class="text-lg font-bold text-white truncate">${name}</h3>
            <p class="text-zinc-400 text-xs mt-0.5">${m.description || ''}</p>
            <p class="routing-label text-orange-400 text-xs font-medium mt-1.5 tracking-wider">${m.routing_label || '—'}</p>
        </div>
        <div class="flex flex-col items-end gap-1.5 shrink-0">
            ${midiLabel ? `<div class="text-xs font-mono bg-zinc-800/80 text-zinc-400 px-2.5 py-1 rounded-lg">${midiLabel}</div>` : ''}
            <!-- LED indicator: dim by default, lights up on trigger -->
            <div id="led-${name}" class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg transition-all duration-150"
                 title="Last trigger time">
              <span id="led-dot-${name}" class="w-2 h-2 rounded-full bg-zinc-700 transition-all duration-150"></span>
              <span id="led-label-${name}" class="text-[10px] font-mono text-zinc-600"></span>
            </div>
        </div>
    </div>
    <div class="h-1.5 bg-zinc-800 rounded-full overflow-hidden mb-4">
      <div id="progress-bar-${name}" class="h-full bg-gradient-to-r from-amber-400 to-orange-500" style="width:0%;"></div>
    </div>
    <div class="grid grid-cols-3 gap-2">
        <button onclick="fireMacro('${name}',1.0,false)"
            class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-400 active:scale-95 active:bg-orange-600 text-black font-bold py-4 rounded-xl text-lg transition-all">
            FIRE
        </button>
        <button onclick="fireMacro('${name}',1.0,true)"
            class="bg-zinc-800 hover:bg-amber-400/20 border border-amber-400/40 hover:border-amber-400 text-amber-400 font-semibold py-4 rounded-xl transition-all active:scale-95 text-xs tracking-widest">
            RAMP
        </button>
    </div>
    <button onclick="toggleDetail('${name}')" class="mt-4 w-full text-zinc-600 hover:text-orange-400 text-[10px] font-medium flex items-center justify-center gap-1 transition-colors tracking-widest">
        ADVANCED <i class="fas fa-chevron-down text-[9px]"></i>
    </button>
    <div id="detail-${name}" class="hidden mt-2 p-3 bg-[#111111] rounded-xl border border-zinc-700/50 font-mono text-xs"></div>
</div>`;
}

// Build cards grouped by workspace → snapshot
function renderCards() {
  const grid = document.getElementById('macro-grid');
  if (!grid) return;

  // Group macros: { workspace: { snapshot: [name, ...] } }
  const groups = {};
  Object.entries(macros).forEach(([name, m]) => {
    const ws = m.workspace || '—';
    const ss = m.snapshot || '—';
    if (!groups[ws]) groups[ws] = {};
    if (!groups[ws][ss]) groups[ws][ss] = [];
    groups[ws][ss].push(name);
  });

  let html = '';
  Object.entries(groups).forEach(([ws, snapshots]) => {
    html += `<div class="col-span-full mb-2">
      <div class="flex items-center gap-3">
        <span class="text-xs font-semibold text-zinc-500 uppercase tracking-widest">${ws}</span>
        <div class="flex-1 h-px bg-zinc-800"></div>
      </div>
    </div>`;
    Object.entries(snapshots).forEach(([ss, names]) => {
      if (ss !== '—') {
        html += `<div class="col-span-full mb-1 ml-1">
          <span class="text-[10px] text-zinc-600 uppercase tracking-widest">↳ ${ss}</span>
        </div>`;
      }
      names.forEach(name => { html += createMacroCardHTML(name, macros[name]); });
    });
  });

  grid.innerHTML = html;
}

// Called by app.js when macro_start WS event arrives — fills bar over exact ramp duration
function animateProgress(name, durationMs) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  bar.offsetHeight; // force reflow
  bar.style.transition = `width ${durationMs}ms linear`;
  bar.style.width = '100%';
}

// Called by app.js when macro_complete WS event arrives — instant reset, no drain animation
function snapProgressToZero(name) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
}

async function fireMacro(name, value = 1.0, ramp = false) {
  const macro = macros[name];
  if (!macro) return;
  // Progress bar is now driven by the macro_start WebSocket event from the server.
  // No client-side animation here — the bar starts when the ramp actually fires.
  try {
    await fetch(`/api/trigger/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ param: value }),
    });
  } catch (e) {
    console.error('[UI] fireMacro error:', e);
  }
}

function toggleDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const m = macros[name];
  if (!panel || !m) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    const duration = (calculateDurationMs(m, false) / 1000).toFixed(1);
    let html = `<div class="space-y-2 text-zinc-300">`;
    html += `<div><span class="text-orange-400">Routing:</span> ${m.routing_label || '—'}</div>`;
    html += `<div><span class="text-orange-400">OSC:</span> <span class="text-amber-300">${m.osc_preview || '—'}</span></div>`;
    html += `<div><span class="text-orange-400">Duration:</span> ${duration}s</div>`;
    if (m.midi_triggers && m.midi_triggers.length) {
      const labels = m.midi_triggers.map(t => `CC${t.number} ch${t.channel}`).join(', ');
      html += `<div><span class="text-orange-400">MIDI:</span> ${labels}</div>`;
    }
    html += `<details class="mt-3"><summary class="cursor-pointer text-orange-400 hover:text-orange-300">Full JSON</summary>
      <pre class="text-[10px] overflow-auto max-h-48 mt-2 text-zinc-400">${JSON.stringify(m, null, 2)}</pre></details>`;
    html += `</div>`;
    panel.innerHTML = html;
  }
}

async function toggleSettingsMenu() {
  const menu = document.getElementById('settings-menu');
  if (!menu) return;
  menu.classList.toggle('hidden');
  if (!menu.classList.contains('hidden')) {
    // Fetch and show what's currently loaded
    try {
      const res = await fetch('/api/status');
      const s = await res.json();
      const info = document.getElementById('settings-status');
      if (info) {
        info.innerHTML =
          `<span class="text-zinc-400">${s.macros} macro${s.macros !== 1 ? 's' : ''}</span>` +
          ` · <span class="text-zinc-400">${s.channel_map_submixes} submix${s.channel_map_submixes !== 1 ? 'es' : ''}</span>`;
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

async function reloadServer() {
  if (confirm('Reload bridge server?')) {
    await fetch('/api/reload', { method: 'POST' });
    location.reload();
  }
}

function uploadFile(input, type) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  fetch(`/api/upload/${type}`, { method: 'POST', body: formData })
    .then(() => location.reload())
    .catch(e => console.error('[UI] uploadFile error:', e));
}
