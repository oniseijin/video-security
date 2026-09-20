from __future__ import annotations

THEME_CSS = """
/* Person-of-Interest surveillance theme: dual polarity.
   MACHINE (dark, default): white + neon red on black, glowing corner brackets.
   SAMARITAN (light): black + crisp red on white, hairline edge + reticle.
   Design language adapted from MIT-licensed references:
   poi-web-ui (c) 2018 Krisztián Kis - Phresh-IT
   positronick-ui (c) 2026 Nicholas Sollazzo
   Framework-free modern CSS; the same tokens carry to the future web UI. */
@import url('https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');

:root,
html[data-theme='machine'] {
  color-scheme: dark;
  --vs-bg: #000000;
  --vs-surface-1: rgba(255, 255, 255, 0.04);
  --vs-surface-2: rgba(255, 255, 255, 0.07);
  --vs-line: #aaa3a3;
  --vs-line-faint: rgba(170, 163, 163, 0.35);
  --vs-ink: #ffffff;
  --vs-ink-dim: rgba(255, 255, 255, 0.6);
  --vs-ink-faint: rgba(255, 255, 255, 0.35);
  --vs-accent: #ff0000;
  --vs-threat: var(--vs-accent);
  --vs-asset: #ffffff;
  --vs-info: rgba(20, 140, 252, 0.9);
  --vs-success: rgba(71, 255, 86, 0.85);
  --vs-warning: #ffc400;
  --vs-selection-bg: rgba(134, 0, 0, 0.6);
  --vs-selection-ink: rgba(255, 0, 0, 0.95);
  --vs-frame-edge: transparent;
  --vs-frame-glow: drop-shadow(0 0 6px var(--tone, var(--vs-ink)));
  --vs-face-glow: 0 0 6px rgba(255, 255, 255, 0.7);
  --vs-reticle: none;
  --vs-rec-glow: 0 0 12px var(--vs-accent);
  --vs-scanline-opacity: 1;
}

html[data-theme='samaritan'] {
  color-scheme: light;
  --vs-bg: #ffffff;
  --vs-surface-1: rgba(0, 0, 0, 0.04);
  --vs-surface-2: rgba(0, 0, 0, 0.06);
  --vs-line: rgba(0, 0, 0, 0.45);
  --vs-line-faint: rgba(0, 0, 0, 0.18);
  --vs-ink: #000000;
  --vs-ink-dim: rgba(0, 0, 0, 0.55);
  --vs-ink-faint: rgba(0, 0, 0, 0.35);
  --vs-accent: #e8000d;
  --vs-threat: var(--vs-accent);
  --vs-asset: #000000;
  --vs-info: var(--vs-ink);
  --vs-success: var(--vs-ink);
  --vs-warning: var(--vs-accent);
  --vs-selection-bg: var(--vs-accent);
  --vs-selection-ink: #ffffff;
  --vs-frame-edge: var(--vs-line);
  --vs-frame-glow: none;
  --vs-face-glow: none;
  --vs-reticle: block;
  --vs-rec-glow: none;
  --vs-scanline-opacity: 0;
}

:root {
  --vs-font-display: 'Barlow Semi Condensed', system-ui, -apple-system, sans-serif;
  --vs-font-mono: 'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace;
  --vs-tracking: 0.08em;
  --vs-radius: 0px;
  --vs-dur-pulse: 1.2s;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background-color: var(--vs-bg);
  color: var(--vs-ink);
  font-family: var(--vs-font-display);
  letter-spacing: var(--vs-tracking);
  min-height: 100vh;
}

body::after {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: var(--vs-scanline-opacity);
  background: repeating-linear-gradient(
    0deg, rgba(255, 255, 255, 0.025) 0 1px, transparent 1px 3px);
}

::selection { color: var(--vs-selection-ink); background: var(--vs-selection-bg); }

.wrap { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }

.masthead {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  border-bottom: 2px solid var(--vs-ink);
  padding: 0.75rem 0;
}
.masthead-title {
  font-size: 1.25rem;
  font-weight: 600;
  text-transform: uppercase;
}
.masthead-meta {
  margin-left: auto;
  font-family: var(--vs-font-mono);
  font-size: 0.75rem;
  color: var(--vs-ink-dim);
  text-transform: uppercase;
}
.rec-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--vs-accent);
  box-shadow: var(--vs-rec-glow);
  animation: vs-pulse var(--vs-dur-pulse) infinite ease-in-out;
  flex: none;
}

.theme-toggle {
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: var(--vs-tracking);
  color: var(--vs-ink-dim);
  background: transparent;
  border: 1px solid var(--vs-line);
  padding: 0.2rem 0.6rem;
  cursor: pointer;
}
.theme-toggle:hover,
.face-toggle:hover {
  color: var(--vs-accent);
  border-color: var(--vs-accent);
}
.face-toggle {
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: var(--vs-tracking);
  color: var(--vs-ink-dim);
  background: transparent;
  border: 1px solid var(--vs-line);
  padding: 0.2rem 0.6rem;
  cursor: pointer;
}
.face-toggle.off { color: var(--vs-ink-faint); border-style: dashed; }

body.hide-faces .face-box { display: none; }

.filepath {
  font-family: var(--vs-font-mono);
  font-size: 0.75rem;
  color: var(--vs-ink-dim);
  word-break: break-all;
  margin: 0.75rem 0 0;
}

.flag {
  font-family: var(--vs-font-mono);
  font-size: 0.75rem;
  color: var(--vs-warning);
  border: 1px solid var(--vs-warning);
  padding: 0.35rem 0.6rem;
  margin: 0.75rem 0 0;
  text-transform: uppercase;
}

.note {
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  color: var(--vs-ink-faint);
  margin: 0.5rem 0 0;
}

.gps-svg {
  display: block;
  width: 100%;
  height: auto;
  color: var(--vs-ink);
  border: 1px solid var(--vs-line);
}
.gps-dot--threat { fill: var(--vs-threat); }
.gps-dot--warning { fill: var(--vs-warning); }
.gps-dot--info { fill: var(--vs-info); }
.gps-dot--asset { fill: var(--vs-asset); }

.panel { margin: 2rem 0; }
.panel > h2 {
  font-size: 0.875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: var(--vs-tracking);
  border-bottom: 1px solid var(--vs-line);
  padding-bottom: 0.35rem;
  margin: 0 0 0.75rem;
}

.data-table { border-collapse: collapse; width: 100%; font-size: 0.8125rem; }
.data-table th {
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: var(--vs-tracking);
  color: var(--vs-ink-dim);
  text-align: left;
  border-bottom: 2px solid var(--vs-line);
  padding: 0.35rem 0.6rem;
}
.data-table td {
  border-bottom: 1px solid var(--vs-line-faint);
  padding: 0.35rem 0.6rem;
  font-family: var(--vs-font-mono);
  vertical-align: top;
}
.data-table tbody tr:hover { background: var(--vs-surface-2); }
.data-table .num { text-align: right; }

.row-threat td { color: var(--vs-threat); }
.row-warning td { color: var(--vs-warning); }
.row-info td { color: var(--vs-info); }
.row-suppressed td { color: var(--vs-ink-faint); }

.subject {
  position: relative;
  margin: 2.25rem 0;
  padding: 0.75rem;
  color: var(--tone, var(--vs-ink));
  border: 1px solid var(--vs-frame-edge);
}
.subject::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  --c: 16px;
  --b: 2px solid currentColor;
  background:
    linear-gradient(currentColor, currentColor) 0 0 / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 0 0 / var(--b) var(--c) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 0 / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 0 / var(--b) var(--c) no-repeat,
    linear-gradient(currentColor, currentColor) 0 100% / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 0 100% / var(--b) var(--c) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 100% / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 100% / var(--b) var(--c) no-repeat;
  filter: var(--vs-frame-glow);
}
.subject::after {
  content: '';
  display: var(--vs-reticle);
  position: absolute;
  top: 50%;
  left: 50%;
  width: 24px;
  height: 24px;
  transform: translate(-50%, -50%);
  pointer-events: none;
  background:
    linear-gradient(currentColor, currentColor) 50% 0 / 1px 100% no-repeat,
    linear-gradient(currentColor, currentColor) 0 50% / 100% 1px no-repeat;
  opacity: 0.6;
}
.subject--threat { --tone: var(--vs-threat); }
.subject--warning { --tone: var(--vs-warning); }
.subject--info { --tone: var(--vs-info); }
.subject--asset { --tone: var(--vs-asset); }
.subject img {
  display: block;
  max-width: 100%;
  height: auto;
}
.designation {
  position: absolute;
  top: -0.7em;
  left: 0.75rem;
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: var(--vs-tracking);
  background: var(--vs-bg);
  color: var(--tone, var(--vs-ink));
  border: 1px solid var(--tone, var(--vs-ink));
  padding: 0.1rem 0.5rem;
}
.subject figcaption {
  font-family: var(--vs-font-mono);
  font-size: 0.75rem;
  color: var(--vs-ink-dim);
  margin-top: 0.6rem;
}

.subject:target { border-color: var(--tone, var(--vs-ink)); }

.kf-wrap { position: relative; }
.face-box {
  position: absolute;
  border: 2px solid var(--vs-asset);
  box-shadow: var(--vs-face-glow);
  pointer-events: none;
}
.face-box::after {
  content: '';
  position: absolute;
  top: -2px;
  left: -2px;
  width: 6px;
  height: 6px;
  border-top: 2px solid var(--vs-accent);
  border-left: 2px solid var(--vs-accent);
}

.chip {
  display: inline-block;
  font-family: var(--vs-font-mono);
  font-size: 0.75rem;
  border: 1px solid var(--vs-line);
  padding: 0.15rem 0.6rem;
  margin: 0.25rem 0.25rem 0.25rem 0;
  color: var(--vs-ink);
}
a.chip { text-decoration: none; }
a.chip:hover { border-color: var(--vs-accent); color: var(--vs-accent); }
.chip--plate { border-color: var(--vs-info); color: var(--vs-info); }

.subject img { cursor: zoom-in; }

.lightbox {
  display: none;
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.85);
  padding: 2rem;
  cursor: zoom-out;
  overflow: hidden;
}
.lightbox.open { display: flex; align-items: center; justify-content: center; }
.lightbox-frame {
  position: relative;
  padding: 1rem;
  color: var(--vs-ink);
  border: 1px solid var(--vs-frame-edge);
}
.lightbox-frame::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  --c: 24px;
  --b: 2px solid currentColor;
  background:
    linear-gradient(currentColor, currentColor) 0 0 / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 0 0 / var(--b) var(--c) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 0 / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 0 / var(--b) var(--c) no-repeat,
    linear-gradient(currentColor, currentColor) 0 100% / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 0 100% / var(--b) var(--c) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 100% / var(--c) var(--b) no-repeat,
    linear-gradient(currentColor, currentColor) 100% 100% / var(--b) var(--c) no-repeat;
  filter: var(--vs-frame-glow);
}
.lightbox-zoom {
  position: relative;
  width: fit-content;
  margin: 0 auto;
  transform-origin: 50% 50%;
}
.lightbox-frame img {
  display: block;
  max-width: 92vw;
  max-height: 80vh;
  width: auto;
  height: auto;
  cursor: default;
}
.lightbox-zoom img.zoomed { cursor: grab; }
.lightbox-zoom img.dragging { cursor: grabbing; }
.lightbox-controls {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.6rem;
}
.lightbox-controls button {
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: var(--vs-tracking);
  color: var(--vs-ink-dim);
  background: transparent;
  border: 1px solid var(--vs-line);
  padding: 0.2rem 0.6rem;
  cursor: pointer;
}
.lightbox-controls button:hover {
  color: var(--vs-accent);
  border-color: var(--vs-accent);
}
.lb-level {
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  color: var(--vs-ink-faint);
  min-width: 3.5rem;
  text-align: center;
}
.lightbox-caption {
  font-family: var(--vs-font-mono);
  font-size: 0.75rem;
  color: var(--vs-ink-dim);
  margin: 0.6rem 0 0;
  text-align: center;
}

.terminal {
  background: var(--vs-surface-1);
  border: 1px solid var(--vs-line);
  font-family: var(--vs-font-mono);
  font-size: 0.8125rem;
  padding: 0.75rem 1rem;
}
.terminal p { margin: 0.25rem 0; }
.terminal .ts { color: var(--vs-ink-faint); }
.terminal .prompt { color: var(--vs-success); }

.report-footer {
  margin: 3rem 0 1.5rem;
  border-top: 1px solid var(--vs-line);
  padding-top: 0.75rem;
  font-family: var(--vs-font-mono);
  font-size: 0.6875rem;
  color: var(--vs-ink-faint);
  text-transform: uppercase;
}

@keyframes vs-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .rec-dot { animation: none; }
}

@media print {
  body { background: #fff; color: #000; }
  body::after { display: none; }
  .theme-toggle { display: none; }
  .lightbox { display: none; }
  .data-table td, .subject figcaption, .filepath { color: #000; }
}
"""

THEME_JS = """
(function () {
  var saved = null;
  try { saved = localStorage.getItem('vs-theme'); } catch (e) {}
  if (saved === 'samaritan') {
    document.documentElement.setAttribute('data-theme', 'samaritan');
  }
})();
"""

THEME_TOGGLE_JS = """
(function () {
  var root = document.documentElement;
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  function label() {
    btn.textContent =
      root.getAttribute('data-theme') === 'samaritan' ? 'Samaritan' : 'Machine';
  }
  btn.addEventListener('click', function () {
    var next =
      root.getAttribute('data-theme') === 'samaritan' ? 'machine' : 'samaritan';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('vs-theme', next); } catch (e) {}
    label();
  });
  label();
})();
"""

THEME_FACES_JS = """
(function () {
  var btn = document.getElementById('face-toggle');
  if (!btn) return;
  var hidden = false;
  try { hidden = localStorage.getItem('vs-faces') === 'off'; } catch (e) {}
  function apply() {
    document.body.classList.toggle('hide-faces', hidden);
    btn.classList.toggle('off', hidden);
    btn.textContent = hidden ? 'Faces Off' : 'Faces On';
    btn.setAttribute('aria-pressed', hidden ? 'false' : 'true');
  }
  btn.addEventListener('click', function () {
    hidden = !hidden;
    try { localStorage.setItem('vs-faces', hidden ? 'off' : 'on'); } catch (e) {}
    apply();
  });
  apply();
})();
"""

THEME_LIGHTBOX_JS = """
(function () {
  var box = document.getElementById('lightbox');
  if (!box) return;
  var frame = box.querySelector('.lightbox-frame');
  var zoomEl = box.querySelector('.lightbox-zoom');
  var img = zoomEl.querySelector('img');
  var cap = box.querySelector('.lightbox-caption');
  var level = box.querySelector('.lb-level');
  var figs = Array.prototype.slice.call(document.querySelectorAll('.subject'));
  var current = -1;
  var scale = 1;
  var tx = 0;
  var ty = 0;
  var dragging = false;
  var lx = 0;
  var ly = 0;

  function apply() {
    if (scale < 1) { scale = 1; }
    if (scale <= 1) { tx = 0; ty = 0; }
    zoomEl.style.transform =
      'translate(' + tx + 'px, ' + ty + 'px) scale(' + scale + ')';
    if (level) { level.textContent = Math.round(scale * 100) + '%'; }
    img.classList.toggle('zoomed', scale > 1);
  }

  function reset() {
    scale = 1;
    tx = 0;
    ty = 0;
    apply();
  }

  function open(i) {
    var fig = figs[i];
    var src = fig.querySelector('img');
    if (!src) return;
    img.src = src.src;
    Array.prototype.slice.call(zoomEl.querySelectorAll('.face-box')).forEach(
      function (el) { el.remove(); }
    );
    var wrap = src.closest('.kf-wrap');
    if (wrap) {
      Array.prototype.slice.call(wrap.querySelectorAll('.face-box')).forEach(
        function (el) { zoomEl.appendChild(el.cloneNode(true)); }
      );
    }
    var fcap = fig.querySelector('figcaption');
    var tag = fig.querySelector('.designation');
    cap.textContent = (fcap ? fcap.textContent : '') ||
      (tag ? tag.textContent : '');
    box.classList.add('open');
    box.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    current = i;
    reset();
  }

  function close() {
    box.classList.remove('open');
    box.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    current = -1;
  }

  function zoomBy(factor, clientX, clientY) {
    var s0 = scale;
    var s1 = Math.min(8, Math.max(1, s0 * factor));
    if (s1 === s0) { return; }
    var cx = 0;
    var cy = 0;
    if (clientX !== undefined) {
      var rect = zoomEl.getBoundingClientRect();
      cx = clientX - (rect.left + rect.width / 2);
      cy = clientY - (rect.top + rect.height / 2);
    }
    tx -= (cx / s0) * (s1 - s0);
    ty -= (cy / s0) * (s1 - s0);
    scale = s1;
    apply();
  }

  figs.forEach(function (fig, i) {
    var inner = fig.querySelector('img');
    if (!inner) return;
    inner.addEventListener('click', function (e) {
      e.preventDefault();
      open(i);
    });
  });

  box.addEventListener('click', function (e) {
    if (e.target === box || e.target === frame || e.target === zoomEl) {
      close();
    }
  });
  img.addEventListener('click', function (e) {
    e.stopPropagation();
  });
  box.addEventListener('wheel', function (e) {
    if (current < 0) { return; }
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 1.25 : 0.8, e.clientX, e.clientY);
  }, { passive: false });

  Array.prototype.slice.call(box.querySelectorAll('[data-zoom]')).forEach(
    function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var mode = btn.getAttribute('data-zoom');
        if (mode === 'in') { zoomBy(1.25); }
        else if (mode === 'out') { zoomBy(0.8); }
        else { reset(); }
      });
    }
  );

  img.addEventListener('dblclick', function () {
    scale = scale > 1 ? 1 : 2.5;
    tx = 0;
    ty = 0;
    apply();
  });
  img.addEventListener('mousedown', function (e) {
    if (scale <= 1) { return; }
    e.preventDefault();
    dragging = true;
    lx = e.clientX;
    ly = e.clientY;
    img.classList.add('dragging');
  });
  document.addEventListener('mousemove', function (e) {
    if (!dragging) { return; }
    tx += e.clientX - lx;
    ty += e.clientY - ly;
    lx = e.clientX;
    ly = e.clientY;
    apply();
  });
  document.addEventListener('mouseup', function () {
    dragging = false;
    img.classList.remove('dragging');
  });

  document.addEventListener('keydown', function (e) {
    if (current < 0) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowRight') open((current + 1) % figs.length);
    if (e.key === 'ArrowLeft') open((current - 1 + figs.length) % figs.length);
  });
})();
"""

THEME_HEAD = (
    "<script>"
    + THEME_JS
    + "</script>"
)


THREAT_EVENTS = {
    "impact",
    "hard_brake",
    "hard_corner",
    "intrusion",
    "audio_distress",
    "dangerous_driving",
    "collision",
}
WARNING_EVENTS = {"suspicious_behavior", "loitering", "loiter"}
INFO_EVENTS = {"plate_capture"}


def event_tone(event_type: str) -> str:
    if event_type in THREAT_EVENTS:
        return "threat"
    if event_type in WARNING_EVENTS:
        return "warning"
    if event_type in INFO_EVENTS:
        return "info"
    return "asset"
