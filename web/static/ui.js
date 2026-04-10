/* ui.js - FINAL FIXED VERSION (April 2026) — clean layout + real macro timing + reliable reset */

function calculateDurationMs(macro, isRamp) {
  if (macro.durationMs) return macro.durationMs;
  // Find the step that has an operation (ramp or lfo)
  const step = macro.steps ? macro.steps.find(s => s.operation) : null;
  if (!step || !step.operation) return isRamp ? 3500 : 2000;
  const op = step.operation;
  const bars = op.bars || 2;
  const bpm = op.bpm || 140;
  const msPerBar = (240000 / bpm); // standard 4/4 bar timing
  return Math.round(bars * msPerBar);
}

function createMacroCardHTML(name, m) {
  return `
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-8 rounded-3xl shadow-2xl">
    <div class="flex justify-between items-start mb-8">
        <div class="flex-1 min-w-0">
            <h3 class="text-3xl font-bold text-white tracking-tight">${name}</h3>
            <p class="text-zinc-400 text-base mt-2 line-clamp-2">${m.description || ''}</p>
            <p class="text-orange-400 text-xs font-medium mt-4 tracking-[0.125em] uppercase">${m.routing_label || '—'}</p>
        </div>
        <div id="last-trigger-${name}" class="midi-badge text-xs font-mono bg-green-500/10 text-green-400 px-5 py-2 rounded-3xl flex items-center gap-1.5 flex-shrink-0"></div>
    </div>
    
    <!-- PROGRESS BAR — thick, prominent, always visible -->
    <div class="h-5 bg-zinc-800 rounded-3xl overflow-hidden mb-10 shadow-inner">
      <div id="progress-bar-${name}" 
           class="h-full bg-gradient-to-r from-orange-400 via-amber-400 to-orange-500 origin-left transition-all"
           style="width: 100%; transform: scaleX(0);"></div>
    </div>
    
    <div class="grid grid-cols-3 gap-4">
        <button onclick="fireMacro('${name}', 1.0, false)" 
                class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-600 active:scale-[0.97] text-black font-bold py-8 rounded-3xl text-3xl transition-all shadow-inner">
            FIRE
        </button>
        <button onclick="fireMacro('${name}', 1.0, true)" 
                class="border-2 border-amber-400 hover:bg-amber-400/10 active:bg-amber-400/20 text-amber-400 font-semibold py-8 rounded-3xl transition-all active:scale-[0.97] shadow-inner text-xl">
            RAMP
        </button>
    </div>
    
    <button onclick="toggleDetail('${name}')" 
            class="mt-10 w-full text-zinc-400 hover:text-orange-400 text-sm font-medium flex items-center justify-center gap-2 py-2">
        DETAILS ▼
    </button>
    <div id="detail-${name}" class="hidden mt-6 p-6 bg-[#111111] rounded-3xl border border-zinc-700 font-mono text-sm"></div>
</div>`;
}

function renderCards() {
  console.log("🔄 renderCards() — building clean UI");
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
  console.log(`[ANIM] Starting for ${name} — ${durationMs}ms (real macro timing)`);
  
  // Force reset + reflow
  bar.style.transition = 'none';
  bar.style.transform = 'scaleX(0)';
  bar.offsetHeight; // force reflow
  
  // Start animation
  bar.style.transition = `transform ${durationMs}ms cubic-bezier(0.4, 0, 0.2, 1)`;
  bar.style.transform = 'scaleX(1)';
  
  // Reliable reset
  setTimeout(() => {
    bar.style.transition = 'transform 200ms cubic-bezier(0.4, 0, 0.2, 1)';
    bar.style.transform = 'scaleX(0)';
    console.log(`[ANIM] Finished and reset for ${name}`);
  }, durationMs + 50);
}

async function fireMacro(name, value = 1.0, ramp = false) {
  const macro = macros[name];
  if (!macro) return;
  console.log(`[UI] Firing macro: ${name} (ramp=${ramp})`);
  
  const durationMs = calculateDurationMs(macro, ramp);
  animateProgress(name, durationMs);
  
  try {
    await fetch(`/api/trigger/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ param: value })
    });
  } catch (err) { console.error(err); }
}

function updateMacroCard(name) {
  console.log(`[UI] Incremental update for ${name}`);
}

function toggleDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const m = macros[name];
  if (!panel || !m) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    let html = `<div class="space-y-5">`;
    html += `<div><strong class="text-orange-400">Routing:</strong> ${m.routing_label || '—'}</div>`;
    html += `<div><strong class="text-orange-400">OSC Preview:</strong> <code class="text-amber-300">${m.osc_preview || '—'}</code></div>`;
    html += `<div><strong class="text-orange-400">Duration:</strong> ${(calculateDurationMs(m, false)/1000).toFixed(1)}s</div>`;
    if (m.midi_triggers && m.midi_triggers.length) {
      html += `<div><strong class="text-orange-400">MIDI Triggers:</strong><ul class="list-disc ml-4">`;
      m.midi_triggers.forEach(t => html += `<li>CC${t.number} ch${t.channel}</li>`);
      html += `</ul></div>`;
    }
    html += `<details class="mt-6"><summary class="cursor-pointer text-orange-400">Full macro JSON</summary><pre class="text-[10px] overflow-auto max-h-64 mt-2 bg-black/30 p-3 rounded-2xl">${JSON.stringify(m, null, 2)}</pre></details>`;
    html += `</div>`;
    panel.innerHTML = html;
  }
}
