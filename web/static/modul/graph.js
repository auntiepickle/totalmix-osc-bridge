/* modul/graph.js — the realtime graphing engine (MODUL rule 5: show the signal).
 *
 * Built on uPlot (vendored, MIT — https://github.com/leeoniya/uPlot): canvas,
 * microsecond redraws, native log scales; the open-source standard for tight
 * realtime plotting. This wrapper owns the MODUL theme and two plot kinds:
 *
 *   filter    — live magnitude response on a log-f axis (20 Hz..20 kHz),
 *               rAF-morphed on value/power changes, cutoff draggable
 *               directly on the plot (writes through knobInput).
 *   waveform  — an operation's shape (ramp curves, LFO cycles) over one
 *               duration; also usable for future realtime streams.
 *
 * Filter math is domain logic and lives here, not in the chart:
 *   highpass/lowpass: Butterworth |H|, order = slope/6 (6..24 dB/oct)
 *   lowpass-q:        resonant biquad LP (EQ band 3 'Low Pass'), Q shows.
 */
(function () {
  const FMIN = 20, FMAX = 20000, DB_TOP = 12, DB_BOT = -30, N = 96;
  const ORANGE = '#ff4d00';
  const XS = Array.from({ length: N + 1 }, (_, i) => FMIN * Math.pow(FMAX / FMIN, i / N));

  function magDb(model, f) {
    if (!model || !model.f) return 0;
    const fc = model.f;
    if (model.kind === 'highpass') {
      const n = model.order || 2;
      return -10 * Math.log10(1 + Math.pow(fc / f, 2 * n));
    }
    if (model.kind === 'lowpass') {
      const n = model.order || 2;
      return -10 * Math.log10(1 + Math.pow(f / fc, 2 * n));
    }
    if (model.kind === 'lowpass-q') {
      const q = Math.max(0.1, model.q || 0.7);
      const r = f / fc;
      return -10 * Math.log10(Math.max(Math.pow(1 - r * r, 2) + Math.pow(r / q, 2), 1e-9));
    }
    if (model.kind === 'highpass-q') {           // resonant HP (EQ band type)
      const q = Math.max(0.1, model.q || 0.7);
      const r = f / fc;
      return 10 * Math.log10(Math.max(Math.pow(r, 4) /
        Math.max(Math.pow(1 - r * r, 2) + Math.pow(r / q, 2), 1e-12), 1e-9));
    }
    if (model.kind === 'bell') {                 // RBJ peaking, analog prototype
      const A = Math.pow(10, (model.gain || 0) / 40);
      const q = Math.max(0.1, model.q || 0.7);
      const r = f / fc, w = 1 - r * r;
      return 10 * Math.log10(
        (w * w + Math.pow(r * A / q, 2)) /
        Math.max(w * w + Math.pow(r / (A * q), 2), 1e-12));
    }
    if (model.kind === 'shelf-hi' || model.kind === 'shelf-lo') {
      const A = Math.pow(10, (model.gain || 0) / 40);
      const q = Math.max(0.3, model.q || 0.7);
      const r = f / fc, sq = Math.sqrt(A) / q * r;
      const hi = model.kind === 'shelf-hi';
      const num = hi ? Math.pow(1 - A * r * r, 2) + sq * sq
                     : Math.pow(A - r * r, 2) + sq * sq;
      const den = hi ? Math.pow(A - r * r, 2) + sq * sq
                     : Math.pow(1 - A * r * r, 2) + sq * sq;
      return 10 * Math.log10(Math.max(A * A * num / Math.max(den, 1e-12), 1e-9));
    }
    return 0;
  }
  const ys = (model, mix) => XS.map(f => magDb(model, f) * mix);

  const fmtHz = f => f >= 9950 ? (f / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
                  : f >= 995 ? (f / 1000).toFixed(2).replace(/0+$/, '').replace(/\.$/, '') + 'k'
                  : Math.round(f) + '';

  const AXIS = {
    stroke: 'rgba(235,232,225,0.45)',
    font: '9px "IBM Plex Mono", ui-monospace, monospace',
    ticks: { show: false },
    grid: { stroke: 'rgba(255,255,255,0.07)', width: 1 },
    size: 14, gap: 2,
  };

  const plots = {};   // key -> {u, model, mix, anim, dragCb}

  function filterInit(key, el, opts = {}) {
    if (plots[key]) { plots[key].u.destroy(); delete plots[key]; }
    const w = el.clientWidth || 280, h = opts.height || 96;
    const u = new uPlot({
      width: w, height: h,
      cursor: { show: false }, legend: { show: false },
      scales: {
        x: { distr: 3, log: 10, range: () => [FMIN, FMAX] },
        y: { range: () => [DB_BOT, DB_TOP] },
      },
      axes: [
        { ...AXIS, splits: () => [100, 1000, 10000], values: (u, s) => s.map(fmtHz) },
        { ...AXIS, side: 3, splits: () => [0, -12, -24], values: (u, s) => s.map(v => v + ''), size: 22 },
      ],
      series: [
        {},
        { stroke: ORANGE, width: 2, fill: 'rgba(255, 77, 0, 0.13)', points: { show: false } },
      ],
      hooks: {
        draw: [u => {
          const st = plots[key];
          if (!st || !st.model || !st.model.f) return;
          // cutoff handle, drawn on-canvas so it is always in sync
          const ctx = u.ctx;
          const x = u.valToPos(st.model.f, 'x', true);
          const y = u.valToPos(magDb(st.model, st.model.f) * st.mix, 'y', true);
          ctx.save();
          ctx.beginPath();
          ctx.arc(x, y, 4.5 * devicePixelRatio, 0, Math.PI * 2);
          ctx.fillStyle = st.mix > 0.5 ? ORANGE : 'rgba(235,232,225,0.35)';
          ctx.strokeStyle = '#141517';
          ctx.lineWidth = 2 * devicePixelRatio;
          ctx.fill(); ctx.stroke();
          ctx.restore();
        }],
      },
    }, [XS, ys(null, 0)], el);

    const st = plots[key] = { u, model: null, mix: 0, anim: 0 };

    // direct manipulation: drag anywhere on the plot = set cutoff
    if (opts.toKnob) {
      const over = u.over;
      let dragging = false;
      const toF = ev => {
        const r = over.getBoundingClientRect();
        const f = u.posToVal(Math.max(0, Math.min(r.width, ev.clientX - r.left)), 'x');
        return Math.max(FMIN, Math.min(FMAX, f));
      };
      const toDb = ev => {
        const r = over.getBoundingClientRect();
        return u.posToVal(Math.max(0, Math.min(r.height, ev.clientY - r.top)), 'y');
      };
      const applyDrag = ev => {
        window.knobInput(opts.name, opts.toKnob(toF(ev)));
        // vertical axis (#user request: the dot moves Q and gain too):
        // resonant LP/HP -> Q (dot height = resonance peak in dB);
        // bell/shelf -> band gain (dot height = the gain you place)
        const st = plots[key], k = st.model && st.model.kind;
        if ((k === 'lowpass-q' || k === 'highpass-q') && opts.setQ) {
          const q = Math.pow(10, toDb(ev) / 20);
          opts.setQ(Math.max(0, Math.min(1, (q - 0.4) / 9.5)));
        } else if ((k === 'bell' || k === 'shelf-hi' || k === 'shelf-lo') && opts.setGain) {
          opts.setGain(Math.max(0, Math.min(1, (toDb(ev) + 20) / 40)));
        }
      };
      over.style.touchAction = 'none';
      over.addEventListener('pointerdown', ev => {
        dragging = true; over.setPointerCapture(ev.pointerId);
        window._knobDrag = opts.dragKey || key;
        applyDrag(ev);
        ev.preventDefault();
      });
      over.addEventListener('pointermove', ev => {
        if (dragging) applyDrag(ev);
      });
      const end = () => {
        dragging = false;
        if (window._knobDrag === (opts.dragKey || key)) window._knobDrag = null;
      };
      over.addEventListener('pointerup', end);
      over.addEventListener('pointercancel', end);
    }

    if (window.ResizeObserver) {
      new ResizeObserver(() => {
        const nw = el.clientWidth;
        if (nw && Math.abs(nw - u.width) > 4) u.setSize({ width: nw, height: h });
      }).observe(el);
    }
    return u;
  }

  // rAF morph: log-space frequency easing + power mix — the motion system
  function filterUpdate(key, model) {
    const st = plots[key];
    if (!st) return;
    const from = { f: (st.model && st.model.f) || model.f, q: (st.model && st.model.q) ?? model.q, mix: st.mix };
    const to = { f: model.f, q: model.q, mix: model.enabled ? 1 : 0 };
    st.model = { ...model };
    const apply = () => st.u.setData([XS, ys(st.model, st.mix)]);
    if (document.hidden ||
        (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) {
      st.model.f = to.f; st.model.q = to.q; st.mix = to.mix; apply(); return;
    }
    cancelAnimationFrame(st.anim);
    const t0 = performance.now(), DUR = 170;
    const ease = t => 1 - Math.pow(1 - t, 3);
    const step = now => {
      const t = Math.min(1, (now - t0) / DUR), e = ease(t);
      st.model.f = Math.exp(Math.log(from.f || to.f) + (Math.log(to.f) - Math.log(from.f || to.f)) * e);
      if (to.q != null && from.q != null) st.model.q = from.q + (to.q - from.q) * e;
      st.mix = from.mix + (to.mix - from.mix) * e;
      apply();
      if (t < 1) st.anim = requestAnimationFrame(step);
    };
    st.anim = requestAnimationFrame(step);
  }

  // ── waveform: an operation's shape over one run (ramps, LFO cycles) ──
  // opShape is PURE (op config -> f(t), both 0..1 domains): part of the
  // portable core, shared by the sampler and the playhead dot.
  function opShape(op) {
    const rng = Array.isArray(op.range) ? op.range.map(Number) : [0, 1];
    const lo = rng[0], span = (rng[1] ?? 1) - lo;
    if (op.type === 'lfo') {
      const cycles = Math.max(1, Math.round((op.bars || 2) * 4 * (op.rate ?? 1)));
      const depth = op.depth ?? 1;
      return t => lo + span * (0.5 - 0.5 * Math.cos(t * cycles * 2 * Math.PI)) * depth;
    }
    if (op.type === 'ramp' && op.curve === 'linear') {
      return t => lo + span * t;
    }
    return t => lo + span * (t < 0.5 ? t * 2 : 2 - t * 2);  // triangle: up & back
  }
  function opSamples(op, n = 160) {
    const xs = Array.from({ length: n + 1 }, (_, i) => i / n);
    const f = opShape(op);
    return [xs, xs.map(f)];
  }

  function waveformInit(key, el, op, opts = {}) {
    if (plots[key]) { plots[key].u.destroy(); delete plots[key]; }
    const u = new uPlot({
      width: el.clientWidth || 240, height: opts.height || 36,
      cursor: { show: false }, legend: { show: false },
      scales: { x: { time: false, range: [0, 1] }, y: { range: [-0.05, 1.05] } },
      axes: [{ show: false }, { show: false }],
      series: [{}, { stroke: ORANGE, width: 1.5, fill: 'rgba(255,77,0,0.10)', points: { show: false } }],
      hooks: {
        draw: [u => {
          // playhead while the operation runs: vertical hairline + a dot
          // riding the curve (the signal, live — rule 5)
          const st = plots[key];
          if (!st || st.progress == null) return;
          const ctx = u.ctx, t = st.progress;
          const x = u.valToPos(t, 'x', true);
          const y = u.valToPos(opShape(st.model)(t), 'y', true);
          ctx.save();
          ctx.strokeStyle = 'rgba(235,232,225,0.28)';
          ctx.lineWidth = 1 * devicePixelRatio;
          ctx.beginPath();
          ctx.moveTo(x, u.valToPos(1.05, 'y', true));
          ctx.lineTo(x, u.valToPos(-0.05, 'y', true));
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(x, y, 3 * devicePixelRatio, 0, Math.PI * 2);
          ctx.fillStyle = ORANGE;
          ctx.strokeStyle = '#141517';
          ctx.lineWidth = 1.5 * devicePixelRatio;
          ctx.fill(); ctx.stroke();
          ctx.restore();
        }],
      },
    }, opSamples(op), el);
    plots[key] = { u, model: op, mix: 1, anim: 0, progress: null };
    if (window.ResizeObserver) {
      new ResizeObserver(() => {
        const nw = el.clientWidth;
        if (nw && Math.abs(nw - u.width) > 4) u.setSize({ width: nw, height: opts.height || 36 });
      }).observe(el);
    }
    return u;
  }
  function waveformUpdate(key, op) {
    const st = plots[key];
    if (st) { st.model = op; st.u.setData(opSamples(op)); }
  }

  // Animate the playhead across one run. onTick(t, value) fires every frame
  // with the operation's CURRENT value (0..1 param-norm) so the shell can
  // show a live readout; onTick(null, null) marks the end (or a stop).
  // Reduced-motion: no playhead, no ticks.
  function waveformRun(key, durationMs, onTick) {
    const st = plots[key];
    if (!st || !durationMs || durationMs <= 0) return;
    if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    cancelAnimationFrame(st.anim);
    st.onTick = onTick || null;
    const shape = opShape(st.model);
    const t0 = performance.now();
    const step = now => {
      const t = (now - t0) / durationMs;
      if (t >= 1 || !plots[key]) { waveformStop(key); return; }
      st.progress = t;
      st.u.redraw();
      if (st.onTick) st.onTick(t, shape(t));
      st.anim = requestAnimationFrame(step);
    };
    st.anim = requestAnimationFrame(step);
  }
  function waveformStop(key) {
    const st = plots[key];
    if (!st) return;
    cancelAnimationFrame(st.anim);
    if (st.progress != null) { st.progress = null; st.u.redraw(); }
    if (st.onTick) { st.onTick(null, null); st.onTick = null; }
  }

  function destroy(key) {
    if (plots[key]) { plots[key].u.destroy(); delete plots[key]; }
  }

  window.ModulGraph = { filterInit, filterUpdate, waveformInit, waveformUpdate, waveformRun, waveformStop, destroy, magDb, fmtHz, opShape };
})();
