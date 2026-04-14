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

function createMacroCardHTML(name, m) {
  return `
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-6 rounded-3xl">
    <div class="flex justify-between items-start mb-6">
        <div class="flex-1">
            <h3 class="text-2xl font-bold text-white">${name}</h3>
            <p class="text-zinc-400 text-sm mt-1">${m.description || ''}</p>
            <p class="routing-label text-orange-400 text-xs font-medium mt-3 tracking-widest">${m.routing_label || '—'}</p>
        </div>
        <div id="last-trigger-${name}" class="midi-badge text-xs font-mono bg-green-500/10 text-green-400 px-4 py-1.5 rounded-2xl flex items-center gap-1"></div>
    </div>
    <div class="h-4 bg-zinc-800 rounded-full overflow-hidden mb-8 border border-zinc-700 shadow-inner">
      <div id="progress-bar-${name}" class="h-full bg-gradient-to-r from-amber-400 to-orange-500" style="width:0%;"></div>
    </div>
    <div class="grid grid-cols-3 gap-3">
        <button onclick="fireMacro('${name}',1.0,false)" class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-600 active:scale-95 text-black font-bold py-7 rounded-3xl text-2xl transition-all shadow-inner">FIRE</button>
        <button onclick="fireMacro('${name}',1.0,true)" class="border-2 border-amber-400 hover:bg-amber-400/10 text-amber-400 font-medium py-7 rounded-3xl transition-all active:scale-95 shadow-inner">RAMP</button>
    </div>
    <button onclick="toggleDetail('${name}')" class="mt-8 w-full text-zinc-400 hover:text-orange-400 text-sm font-medium flex items-center justify-center gap-2">DETAILS ▼</button>
    <div id="detail-${name}" class="hidden mt-4 p-4 bg-[#111111] rounded-3xl border border-zinc-700 font-mono text-sm"></div>
</div>`;
}

function renderCards() {
  const grid = document.getElementById('macro-grid');
  if (!grid) return;
  let html = '';
  Object.keys(macros).forEach(name => html += createMacroCardHTML(name, macros[name]));
  grid.innerHTML = html;
}

function animateProgress(name, durationMs) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  bar.offsetHeight; // force reflow
  bar.style.transition = `width ${durationMs}ms cubic-bezier(0.4,0,0.2,1)`;
  bar.style.width = '100%';
  setTimeout(() => {
    bar.style.transition = 'width 300ms cubic-bezier(0.4,0,0.2,1)';
    bar.style.width = '0%';
  }, durationMs);
}

async function fireMacro(name, value = 1.0, ramp = false) {
  const macro = macros[name];
  if (!macro) return;
  animateProgress(name, calculateDurationMs(macro, ramp));
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
    let html = `<div class="space-y-4">`;
    html += `<div><strong class="text-orange-400">Routing:</strong> ${m.routing_label || '—'}</div>`;
    html += `<div><strong class="text-orange-400">OSC Preview:</strong> <code class="text-amber-300">${m.osc_preview || '—'}</code></div>`;
    html += `<div><strong class="text-orange-400">Duration:</strong> ${(calculateDurationMs(m, false) / 1000).toFixed(1)}s</div>`;
    if (m.midi_triggers && m.midi_triggers.length) {
      html += `<div><strong class="text-orange-400">MIDI Triggers:</strong><ul class="list-disc ml-4">`;
      m.midi_triggers.forEach(t => { html += `<li>CC${t.number} ch${t.channel}</li>`; });
      html += `</ul></div>`;
    }
    html += `<details class="mt-4"><summary class="cursor-pointer text-orange-400">Full macro JSON</summary><pre class="text-[10px] overflow-auto max-h-64 mt-2">${JSON.stringify(m, null, 2)}</pre></details>`;
    html += `</div>`;
    panel.innerHTML = html;
  }
}

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
