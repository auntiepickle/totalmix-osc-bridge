/* modul/knob.js — a rotary control that is actually operable (MODUL rule 6).
 *
 * 270° travel (-135°..+135°), orange value arc over a grey track, pointer
 * line on the cap. Drag vertically (shift = fine), scroll, double-click
 * snaps to the device value. Writes go through the existing knobInput
 * coalescer; .set() follows feedback with mechanical easing (CSS).
 */
(function () {
  const A0 = -135, A1 = 135;          // degrees
  const R = 15, CX = 18, CY = 18;     // 36px viewBox

  const polar = (deg) => {
    const rad = (deg - 90) * Math.PI / 180;
    return [CX + R * Math.cos(rad), CY + R * Math.sin(rad)];
  };
  function arcPath(fromDeg, toDeg) {
    const [x0, y0] = polar(fromDeg), [x1, y1] = polar(toDeg);
    const large = (toDeg - fromDeg) > 180 ? 1 : 0;
    return `M${x0.toFixed(2)},${y0.toFixed(2)} A${R},${R} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
  }

  function html(name, opts = {}) {
    const size = opts.size === 'mini' ? 24 : 44;
    const cls = opts.size === 'mini' ? 'mk mk-mini' : 'mk';
    const suffix = opts.suffix ? `-${opts.suffix}` : '';
    return `<svg id="mknob-${name}${suffix}" class="${cls}" width="${size}" height="${size}" viewBox="0 0 36 36"
        tabindex="0" role="slider" aria-label="${opts.label || name}">
      <path class="mk-track" d="${arcPath(A0, A1)}"/>
      <path id="mknob-arc-${name}${suffix}" class="mk-arc" d=""/>
      <circle class="mk-cap" cx="${CX}" cy="${CY}" r="10.5"/>
      <line id="mknob-ptr-${name}${suffix}" class="mk-ptr" x1="${CX}" y1="${CY - 4}" x2="${CX}" y2="${CY - 9.5}"/>
    </svg>`;
  }

  function set(name, value, suffix) {
    const sfx = suffix ? `-${suffix}` : '';
    const v = Math.max(0, Math.min(1, +value || 0));
    const arc = document.getElementById(`mknob-arc-${name}${sfx}`);
    const ptr = document.getElementById(`mknob-ptr-${name}${sfx}`);
    if (!arc || !ptr) return;
    const deg = A0 + v * (A1 - A0);
    arc.setAttribute('d', v <= 0.004 ? '' : arcPath(A0, deg));
    ptr.setAttribute('transform', `rotate(${deg} 18 18)`);
  }

  // opts: { get: () -> 0..1, send: (v) -> void, reset: () -> 0..1 | null }
  function wire(name, opts, suffix) {
    const sfx = suffix ? `-${suffix}` : '';
    const el = document.getElementById(`mknob-${name}${sfx}`);
    if (!el || el._mkWired) return;
    el._mkWired = true;
    let dragging = false, y0 = 0, v0 = 0;
    el.addEventListener('pointerdown', ev => {
      dragging = true; y0 = ev.clientY; v0 = opts.get();
      window._knobDrag = suffix ? `${name}:${suffix}` : name;
      el.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    el.addEventListener('pointermove', ev => {
      if (!dragging) return;
      const span = ev.shiftKey ? 900 : 220;     // px of travel for full range
      const v = Math.max(0, Math.min(1, v0 + (y0 - ev.clientY) / span));
      set(name, v, suffix);
      opts.send(v);
    });
    const end = () => {
      dragging = false;
      const key = suffix ? `${name}:${suffix}` : name;
      if (window._knobDrag === key) window._knobDrag = null;
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('wheel', ev => {
      ev.preventDefault();
      const step = ev.shiftKey ? 0.005 : 0.02;
      const v = Math.max(0, Math.min(1, opts.get() + (ev.deltaY < 0 ? step : -step)));
      set(name, v, suffix);
      opts.send(v);
    }, { passive: false });
    el.addEventListener('dblclick', () => {
      const v = opts.reset ? opts.reset() : null;
      if (v != null) { set(name, v, suffix); opts.send(v); }
    });
    el.addEventListener('keydown', ev => {
      const d = ev.key === 'ArrowUp' || ev.key === 'ArrowRight' ? 0.02
             : ev.key === 'ArrowDown' || ev.key === 'ArrowLeft' ? -0.02 : 0;
      if (!d) return;
      ev.preventDefault();
      const v = Math.max(0, Math.min(1, opts.get() + d));
      set(name, v, suffix);
      opts.send(v);
    });
  }

  window.ModulKnob = { html, set, wire };
})();
