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
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-6 rounded-3xl">
    <div class="flex justify-between items-start mb-4">
        <div class="flex-1 min-w-0 pr-3">
            <h3 class="text-2xl font-bold text-white truncate">${name}</h3>
            <p class="text-zinc-400 text-sm mt-1">${m.description || ''}</p>
            <p class="routing-label text-orange-400 text-xs font-medium mt-2 tracking-widest">${m.routing_label || '—'}</p>
        </div>
        <div class="flex flex-col items-end gap-1 shrink-0">
            ${midiLabel ? `<div class="text-xs font-mono bg-zinc-800 text-zinc-400 px-3 py-1 rounded-xl">${midiLabel}</div>` : ''}
            <div id="last-trigger-${name}" class="text-xs font-mono text-zinc-600 px-3 py-1 rounded-xl"></div>
        </div>
    </div>
    <div class="h-2 bg-zinc-800 rounded-full overflow-hidden mb-6 border border-zinc-700/50">
      <div id="progress-bar-${name}" class="h-full bg-gradient-to-r from-amber-400 to-orange-500" style="width:0%;"></div>
    </div>
    <div class="grid grid-cols-3 gap-3">
        <button onclick="fireMacro('${name}',1.0,false)"
            class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-400 active:scale-95 active:bg-orange-600 text-black font-bold py-5 rounded-2xl text-xl transition-all">
            FIRE
        </button>
        <button onclick="fireMacro('${name}',1.0,true)"
            class="bg-zinc-800 hover:bg-amber-400/20 border border-amber-400/50 hover:border-amber-400 text-amber-400 font-semibold py-5 rounded-2xl transition-all active:scale-95 text-sm">
            RAMP
        </button>
    </div>
    <button onclick="toggleDetail('${name}')" class="mt-5 w-full text-zinc-500 hover:text-orange-400 text-xs font-medium flex items-center justify-center gap-1 transition-colors">
        ADVANCED <i class="fas fa-chevron-down text-[10px]"></i>
    </button>
    <div id="detail-${name}" class="hidden mt-3 p-4 bg-[#111111] rounded-2xl border border-zinc-700/50 font-mono text-xs"></div>
</div>`;
}

function renderCards() {
  const grid = document.getElementById('macro-grid');
  if (!grid) return;
  let html = '';
  Object.keys(macros).forEach(name => html += createMacroCardHTML(name, macros[name]));
  grid.innerHTML = html;
}

// Called by app.js when macro_start WS event arrives (server-side timing)
function animateProgress(name, durationMs) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  bar.offsetHeight; // force reflow
  bar.style.transition = `width ${durationMs}ms cubic-bezier(0.4,0,0.2,1)`;
  bar.style.width = '100%';
}

// Called by app.js when macro_complete WS event arrives
function resetProgress(name) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  bar.style.transition = 'width 400ms cubic-bezier(0.4,0,0.2,1)';
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

function toggleSettingsMenu() {
  const menu = document.getElementById('settings-menu');
  if (!menu) return;
  menu.classList.toggle('hidden');
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
