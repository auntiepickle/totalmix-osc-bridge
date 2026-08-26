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
  // Scale per EQ-display convention (researched: FabFilter Pro-Q ranges
  // are SYMMETRIC around 0 dB - +/-3/6/12/30; RME's own channel EQ gain
  // is +/-20 dB and Q 9.9 peaks at +19.9 dB): +/-24 dB fits everything
  // the device can produce, centered on an emphasized 0 dB line. The old
  // -30..+12 clipped boosts and resonance off the top of the display.
  const FMIN = 20, FMAX = 20000, DB_TOP = 24, DB_BOT = -24, N = 96;
  const ORANGE = '#ff4d00';
  // Per-plot frequency window (#user report: a lo-cut that can never go
  // above 500 Hz was squeezed into the left third of a 20 Hz-20 kHz
  // display). Each module shows the range its parameter can IMPACT.
  const logSpace = (lo, hi) =>
    Array.from({ length: N + 1 }, (_, i) => lo * Math.pow(hi / lo, i / N));
  // 1-2-5 gridline positions inside a window; label density adapts:
  // narrow windows (< 2 decades) label 1x and 5x, wide ones decades only
  const gridFor = (lo, hi) => {
    const out = [];
    for (let d = 1; d <= 100000; d *= 10) {
      for (const m of [1, 2, 5]) {
        const f = m * d;
        if (f > lo * 1.15 && f < hi * 0.87) out.push(f);
      }
    }
    return out;
  };
  const labelFor = (lo, hi) => {
    const narrow = hi / lo < 100;
    return f => {
      const mant = f / Math.pow(10, Math.floor(Math.log10(f) + 1e-9));
      const isDecade = Math.abs(mant - 1) < 1e-6;
      const isFive = Math.abs(mant - 5) < 1e-6;
      return (isDecade || (narrow && isFive)) ? fmtHz(f) : '';
    };
  };

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
  const ys = (model, mix, xs) => xs.map(f => magDb(model, f) * mix);

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
    const fr = Array.isArray(opts.frange) ? opts.frange : [FMIN, FMAX];
    const flo = Math.max(1, fr[0]), fhi = Math.min(100000, fr[1]);
    const xs = logSpace(flo, fhi);
    const label = opts.noLabels ? (() => '') : labelFor(flo, fhi);
    const u = new uPlot({
      width: w, height: h,
      cursor: { show: false }, legend: { show: false },
      scales: {
        x: { distr: 3, log: 10, range: () => [flo, fhi] },
        y: { range: () => [DB_BOT, DB_TOP] },
      },
      axes: [
        // 1-2-5 frequency grid (EQ convention) inside THIS module's window
        { ...AXIS, splits: () => gridFor(flo, fhi),
          filter: (u, splits) => splits,   // we control density; uPlot's
          values: (u, s) => s.map(label) },  // space filter ate 50/500
        { ...AXIS, side: 3, splits: () => [-24, -12, 0, 12, 24],
          values: (u, s) => s.map(v => (opts.noLabels ? '' : (v === 0 ? '0' : v === 24 ? '+24' : v === -24 ? '-24' : ''))),
          size: opts.noLabels ? 4 : 22 },
      ],
      series: [
        {},
        { stroke: ORANGE, width: 2, fill: 'rgba(255, 77, 0, 0.13)', points: { show: false } },
      ],
      hooks: {
        draw: [u => {
          const st = plots[key];
          // the knob's LIMITS (#user request): dim the unreachable region,
          // dashed hairlines at the bounds. Drawn over the curve so the
          // out-of-reach part of the response reads as locked.
          if (st && Array.isArray(st.bounds)) {
            const ctx = u.ctx, b = u.bbox;
            const x0 = u.valToPos(st.bounds[0], 'x', true);
            const x1 = u.valToPos(st.bounds[1], 'x', true);
            ctx.save();
            ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
            if (x0 > b.left + 1) ctx.fillRect(b.left, b.top, x0 - b.left, b.height);
            if (x1 < b.left + b.width - 1) ctx.fillRect(x1, b.top, b.left + b.width - x1, b.height);
            ctx.strokeStyle = 'rgba(235, 232, 225, 0.28)';
            ctx.lineWidth = 1 * devicePixelRatio;
            ctx.setLineDash([3 * devicePixelRatio, 3 * devicePixelRatio]);
            for (const x of [x0, x1]) {
              if (x > b.left + 1 && x < b.left + b.width - 1) {
                ctx.beginPath();
                ctx.moveTo(x, b.top);
                ctx.lineTo(x, b.top + b.height);
                ctx.stroke();
              }
            }
            ctx.restore();
          }
          // the 0 dB line is the reference every EQ emphasizes
          {
            const ctx = u.ctx;
            const y0 = u.valToPos(0, 'y', true);
            ctx.save();
            ctx.strokeStyle = 'rgba(235, 232, 225, 0.22)';
            ctx.lineWidth = 1 * devicePixelRatio;
            ctx.beginPath();
            ctx.moveTo(u.bbox.left, y0);
            ctx.lineTo(u.bbox.left + u.bbox.width, y0);
            ctx.stroke();
            ctx.restore();
          }
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
    }, [xs, ys(null, 0, xs)], el);

    const st = plots[key] = { u, model: null, mix: 0, anim: 0, xs, flo, fhi };
    if (Array.isArray(opts.bounds)) {
      const b0 = Math.max(flo, opts.bounds[0]), b1 = Math.min(fhi, opts.bounds[1]);
      // draw only when the limits actually bite (inside the window)
      if (b0 / flo > 1.03 || fhi / b1 > 1.03) st.bounds = [b0, b1];
    }

    // direct manipulation: drag anywhere on the plot = set cutoff
    if (opts.toKnob || opts.onFreq) {
      const over = u.over;
      let dragging = false;
      const toF = ev => {
        const r = over.getBoundingClientRect();
        const f = u.posToVal(Math.max(0, Math.min(r.width, ev.clientX - r.left)), 'x');
        return Math.max(flo, Math.min(fhi, f));
      };
      const toDb = ev => {
        const r = over.getBoundingClientRect();
        return u.posToVal(Math.max(0, Math.min(r.height, ev.clientY - r.top)), 'y');
      };
      // vertical axis (#user request: the dot moves Q and gain too).
      // RELATIVE from the grab point (#user report: it jumped a lot -
      // absolute mapping teleported Q/gain to wherever you touched):
      // grabbing changes nothing vertically; moving up/down applies the
      // dB delta to the value you started from.
      let vStart = null, moved = false, downXY = null;
      const kindNow = () => {
        const st = plots[key], k = st.model && st.model.kind;
        return {
          st,
          resonant: (k === 'lowpass-q' || k === 'highpass-q') && opts.setQ,
          gained: (k === 'bell' || k === 'shelf-hi' || k === 'shelf-lo') && opts.setGain,
        };
      };
      const applyDrag = (ev, isDown) => {
        if (opts.onFreq) opts.onFreq(toF(ev));
        else if (opts.toKnob) window.knobInput(opts.name, opts.toKnob(toF(ev)));
        const { st, resonant, gained } = kindNow();
        if (isDown) {
          vStart = (resonant || gained)
            ? { db: toDb(ev), q: st.model.q || 0.7, gain: st.model.gain || 0 }
            : null;
          return;
        }
        if (!vStart) return;
        const dDb = toDb(ev) - vStart.db;
        if (resonant) {
          const q = vStart.q * Math.pow(10, dDb / 20);   // dB delta on the peak
          opts.setQ(Math.max(0, Math.min(1, (q - 0.4) / 9.5)));
        } else if (gained) {
          opts.setGain(Math.max(0, Math.min(1, (vStart.gain + dDb + 20) / 40)));
        }
      };
      over.style.touchAction = 'none';
      over.addEventListener('pointerdown', ev => {
        dragging = true; over.setPointerCapture(ev.pointerId);
        window._knobDrag = opts.dragKey || key;
        moved = false; downXY = [ev.clientX, ev.clientY];
        applyDrag(ev, true);
        ev.preventDefault();
      });
      over.addEventListener('pointermove', ev => {
        if (!dragging) return;
        if (downXY && Math.hypot(ev.clientX - downXY[0], ev.clientY - downXY[1]) > 4) moved = true;
        applyDrag(ev, false);
      });
      const end = () => {
        dragging = false;
        if (window._knobDrag === (opts.dragKey || key)) window._knobDrag = null;
      };
      over.addEventListener('pointerup', ev => {
        // TAP (no movement) = PLACE the dot at the tap point (#user
        // request): absolute on both axes. Frequency already landed on
        // pointerdown; apply the vertical absolutely here. Drags stay
        // relative (no teleport mid-gesture).
        if (dragging && !moved) {
          const { resonant, gained } = kindNow();
          const db = toDb(ev);
          if (resonant) {
            const q = Math.pow(10, db / 20);   // dot height = resonance peak
            opts.setQ(Math.max(0, Math.min(1, (q - 0.4) / 9.5)));
          } else if (gained) {
            opts.setGain(Math.max(0, Math.min(1, (db + 20) / 40)));
          }
        }
        end();
      });
      over.addEventListener('pointercancel', end);
      // WHEEL = Q (#user request): scroll over the plot narrows/widens
      // the peak without touching frequency or gain. Multiplicative per
      // notch - Q is perceived logarithmically, like frequency.
      over.addEventListener('wheel', ev => {
        if (!opts.setQ) return;
        const st2 = plots[key], k = st2.model && st2.model.kind;
        if (!k || k === 'highpass' || k === 'lowpass') return;  // no Q axis
        ev.preventDefault();
        const dy = ev.deltaMode === 1 ? ev.deltaY * 33 : ev.deltaY;
        const q = (st2.model.q || 0.7) * Math.pow(1.09, -dy / 100);
        opts.setQ(Math.max(0, Math.min(1, (q - 0.4) / 9.5)));
      }, { passive: false });
    }

    if (window.ResizeObserver) {
      new ResizeObserver(() => {
        const nw = el.clientWidth;
        if (nw && Math.abs(nw - u.width) > 4) u.setSize({ width: nw, height: h });
      }).observe(el);
    }
    return u;
  }

  // rAF morph: log-space frequency easing + power mix — the motion system.
  // opts.instant skips the morph entirely: drag frames are DIRECT
  // manipulation (the finger is the animation — easing reads as lag).
  function filterUpdate(key, model, opts) {
    const st = plots[key];
    if (!st) return;
    const from = { f: (st.model && st.model.f) || model.f, q: (st.model && st.model.q) ?? model.q, mix: st.mix };
    const to = { f: model.f, q: model.q, mix: model.enabled ? 1 : 0 };
    st.model = { ...model };
    const apply = () => st.u.setData([st.xs, ys(st.model, st.mix, st.xs)]);
    if ((opts && opts.instant) || document.hidden ||
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

  // ── level strip: volume/pan knobs — the value on a real axis ──────
  // x = param-norm 0..1; opts.axis = {splits:[x...], labels:[s...]} lets
  // the shell speak dB (fader law) or L/C/R; opts.zero marks unity/center;
  // opts.fill 'left'|'center'; opts.bounds dims the unreachable region;
  // opts.onX(v01) receives absolute drags/taps.
  function levelInit(key, el, opts = {}) {
    if (plots[key]) { plots[key].u.destroy(); delete plots[key]; }
    const w = el.clientWidth || 280, h = opts.height || 96;
    const ax = opts.axis || { splits: [0, 0.5, 1], labels: ['0', '', '1'] };
    const u = new uPlot({
      width: w, height: h,
      cursor: { show: false }, legend: { show: false },
      scales: { x: { time: false, range: [0, 1] }, y: { range: [0, 1] } },
      axes: [
        { ...AXIS, splits: () => ax.splits,
          filter: (u, sp) => sp,
          values: (u, sp) => sp.map(x => { if (opts.noLabels) return ''; const i = ax.splits.indexOf(x); return i >= 0 ? ax.labels[i] : ''; }) },
        { show: false },
      ],
      series: [{}, {}],
      hooks: {
        draw: [u => {
          const st = plots[key];
          if (!st) return;
          const ctx = u.ctx, b = u.bbox;
          const xPos = t => u.valToPos(Math.max(0, Math.min(1, t)), 'x', true);
          ctx.save();
          // the value: an orange fill from the anchor to the position
          const v = st.level == null ? 0 : st.level;
          const anchor = opts.fill === 'center' ? 0.5 : 0;
          const x0 = xPos(Math.min(anchor, v)), x1 = xPos(Math.max(anchor, v));
          const mid = b.top + b.height / 2, barH = Math.min(26 * devicePixelRatio, b.height * 0.34);
          ctx.fillStyle = st.mix > 0.5 ? 'rgba(255,77,0,0.28)' : 'rgba(235,232,225,0.10)';
          ctx.fillRect(x0, mid - barH / 2, Math.max(1, x1 - x0), barH);
          ctx.fillStyle = st.mix > 0.5 ? ORANGE : 'rgba(235,232,225,0.35)';
          ctx.fillRect(xPos(v) - 1.5 * devicePixelRatio, mid - barH / 2, 3 * devicePixelRatio, barH);
          // zero/unity reference tick, emphasized like the 0 dB line
          if (opts.zero != null) {
            ctx.strokeStyle = 'rgba(235,232,225,0.30)';
            ctx.lineWidth = 1 * devicePixelRatio;
            ctx.beginPath();
            ctx.moveTo(xPos(opts.zero), b.top);
            ctx.lineTo(xPos(opts.zero), b.top + b.height);
            ctx.stroke();
          }
          // knob limits: dim + dashed hairlines (same language as filters)
          if (Array.isArray(st.bounds)) {
            const bx0 = xPos(st.bounds[0]), bx1 = xPos(st.bounds[1]);
            ctx.fillStyle = 'rgba(0,0,0,0.45)';
            if (bx0 > b.left + 1) ctx.fillRect(b.left, b.top, bx0 - b.left, b.height);
            if (bx1 < b.left + b.width - 1) ctx.fillRect(bx1, b.top, b.left + b.width - bx1, b.height);
            ctx.strokeStyle = 'rgba(235,232,225,0.28)';
            ctx.setLineDash([3 * devicePixelRatio, 3 * devicePixelRatio]);
            for (const x of [bx0, bx1]) {
              if (x > b.left + 1 && x < b.left + b.width - 1) {
                ctx.beginPath(); ctx.moveTo(x, b.top); ctx.lineTo(x, b.top + b.height); ctx.stroke();
              }
            }
            ctx.setLineDash([]);
          }
          // live peak meter (#meters): a slim ink bar above the value
          // lane - the SIGNAL arriving, distinct from the SET level
          if (st.meter != null) {
            const anchor2 = opts.fill === 'center' ? 0.5 : 0;
            const mx0 = xPos(Math.min(anchor2, st.meter));
            const mx1 = xPos(Math.max(anchor2, st.meter));
            const my = mid - barH / 2 - 6 * devicePixelRatio;
            ctx.fillStyle = 'rgba(235, 232, 225, 0.35)';
            ctx.fillRect(mx0, my, Math.max(1, mx1 - mx0), 3 * devicePixelRatio);
          }
          // the handle dot rides the bar end
          ctx.beginPath();
          ctx.arc(xPos(v), mid, 4.5 * devicePixelRatio, 0, Math.PI * 2);
          ctx.fillStyle = st.mix > 0.5 ? ORANGE : 'rgba(235,232,225,0.35)';
          ctx.strokeStyle = '#141517';
          ctx.lineWidth = 2 * devicePixelRatio;
          ctx.fill(); ctx.stroke();
          ctx.restore();
        }],
      },
    }, [[0, 1], [null, null]], el);

    const st = plots[key] = { u, level: null, mix: 1, anim: 0 };
    if (Array.isArray(opts.bounds)) {
      const b0 = Math.max(0, opts.bounds[0]), b1 = Math.min(1, opts.bounds[1]);
      if (b0 > 0.005 || b1 < 0.995) st.bounds = [b0, b1];
    }
    if (opts.onX) {
      const over = u.over;
      let dragging = false;
      const toV = ev => {
        const r = over.getBoundingClientRect();
        return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
      };
      over.style.touchAction = 'none';
      over.addEventListener('pointerdown', ev => {
        dragging = true; over.setPointerCapture(ev.pointerId);
        window._knobDrag = opts.dragKey || key;
        opts.onX(toV(ev));
        ev.preventDefault();
      });
      over.addEventListener('pointermove', ev => { if (dragging) opts.onX(toV(ev)); });
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
  function levelUpdate(key, model) {
    const st = plots[key];
    if (!st) return;
    st.level = model.v;
    st.mix = model.enabled === false ? 0 : 1;
    st.u.redraw();
  }
  // meter-only refresh: does not disturb the set level
  function meterUpdate(key, pos) {
    const st = plots[key];
    if (!st || st.meter === pos) return;
    st.meter = pos;
    st.u.redraw();
  }

  function destroy(key) {
    if (plots[key]) { plots[key].u.destroy(); delete plots[key]; }
  }

  window.ModulGraph = { filterInit, filterUpdate, levelInit, levelUpdate, meterUpdate, waveformInit, waveformUpdate, waveformRun, waveformStop, destroy, magDb, fmtHz, opShape };
})();
