/* ui.js - M2 PHASE 2 COMPLETE (April 2026) — safe progress bar + full card redesign per MD checklist */
/* Items 2-5 knocked out: better spacing, glanceable info, badge alignment, polished buttons */

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
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-8 rounded-3xl shadow-xl">
    <div class="flex justify-between items-start mb-6">
        <div class="flex-1 min-w-0">
            <h3 class="text-2xl font-bold text-white truncate">${name}</h3>
            <p class="text-zinc-400 text-sm mt-1 line-clamp-2">${m.description || ''}</p>
            <p class="text-orange-400 text-xs font-medium mt-3 tracking-widest">${m.routing_label || '—'}</p>
        </div>
        <div id="last-trigger-${name}" class="midi-badge text-xs font-mono bg-green-500/10 text-green-400 px-4 py-1.5 rounded-2xl flex items-center gap-1 flex-shrink-0 ml-6"></div>
    </div>

    <!-- PROGRESS BAR — safe, no conflicting inline height (outer border only, reversed gradient) -->
    <div class="h-4 bg-zinc-800 rounded-full overflow-hidden mb-8 border border-zinc-700 shadow-inner">
      <div id="progress-bar-${name}"
           class="h-full bg-gradient-to-r from-amber-400 to-orange-500 transition-all"
           style="width: 0%;"></div>
    </div>

    <!-- Glanceable info row (MD item 3) -->
    <div class="grid grid-cols-3 text-xs mb-8 text-zinc-400 font-mono">
        <div><span class="text-orange-400">OSC</span> ${m.osc_preview || '—'}</div>
        <div><span class="text-orange-400">DUR</span> ${(calculateDurationMs(m, false)/1000).toFixed(1)}s</div>
        <div><span class="text-orange-400">BPM</span> ${m.steps?.[0]?.operation?.bpm || 140}</div>
    </div>

    <div class="grid grid-cols-3 gap-3">
        <button onclick="fireMacro('${name}', 1.0, false)"
                class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-600 active:scale-[0.97] text-black font-bold py-7 rounded-3xl text-2xl transition-all shadow-inner flex items-center justify-center gap-3">
            🔥 FIRE
        </button>
        <button onclick="fireMacro('${name}', 1.0, true)"
                class="border-2 border-amber-400 hover:bg-amber-400/10 hover:text-amber-300 text-amber-400 font-medium py-7 rounded-3xl transition-all active:scale-[0.97] shadow-inner">
            RAMP
        </button>
    </div>

    <button onclick="toggleDetail('${name}')"
            class="mt-8 w-full text-zinc-400 hover:text-orange-400 text-sm font-medium flex items-center justify-center gap-2">
        DETAILS ▼
    </button>
    <div id="detail-${name}" class="hidden mt-4 p-4 bg-[#111111] rounded-3xl border border-zinc-700 font-mono text-sm"></div>
</div>`;
}

function renderCards() {
  console.log("🔄 renderCards() — M2 Phase 2 complete");
  const grid = document.getElementById('macro-grid');
  if (!grid) return;
  let html = '';
  Object.keys(macros).forEach(name => {
    html += createMacroCardHTML(name, macros[name]);
  });
  grid.innerHTML = html;
}

function animateProgress(name, durationMs) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  console.log(`[ANIM] Starting ${name} — ${durationMs}ms`);

  bar.style.transition = 'none';
  bar.style.width = '0%';
  void bar.offsetHeight; // force repaint

  bar.style.transition = `width ${durationMs}ms cubic-bezier(0.4, 0, 0.2, 1)`;
  bar.style.width = '100%';

  setTimeout(() => {
    bar.style.transition = 'width 300ms cubic-bezier(0.4, 0, 0.2, 1)';
    bar.style.width = '0%';
    console.log(`[ANIM] Finished + reset for ${name}`);
  }, durationMs);
}

async function fireMacro(name, value = 1.0, ramp = false) {
  const macro = macros[name];
  if (!macro) return;
  console.log(`[UI] Firing macro: ${name} (ramp=${ramp})`);

  const durationMs = calculateDurationMs(macro, ramp);
  animateProgress(name, durationMs);

  // TODO (item 6): add loading spinner here in next patch
  try {
    await fetch(`/api/trigger/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ param: value })
    });
  } catch (err) { console.error(err); }
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
    html += `<div><strong class="text-orange-400">Duration:</strong> ${(calculateDurationMs(m, false)/1000).toFixed(1)}s</div>`;
    if (m.midi_triggers && m.midi_triggers.length) {
      html += `<div><strong class="text-orange-400">MIDI Triggers:</strong><ul class="list-disc ml-4">`;
      m.midi_triggers.forEach(t => html += `<li>CC${t.number} ch${t.channel}</li>`);
      html += `</ul></div>`;
    }
    html += `<details class="mt-4"><summary class="cursor-pointer text-orange-400">Full macro JSON</summary><pre class="text-[10px] overflow-auto max-h-64 mt-2">${JSON.stringify(m, null, 2)}</pre></details>`;
    html += `</div>`;
    panel.innerHTML = html;
  }
}
