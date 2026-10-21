/* Home for The Frame. Written for Tizen 6.0's engine (Chrome 76): no ?. or ??, no flex gap.
 * Two states: "lock" (wallpaper, glass clock, an up button) and "apps" (bento grid).
 * Performance rules, measured on the TV:
 *  - Key presses touch only the tiles that change; tile geometry is measured once.
 *  - Everything that moves or fades is its own layer; nothing full-screen animates per press.
 *  - The wallpaper video plays only while Home is on screen, and retries if the decoder is busy. */
(function () {
  'use strict';

  var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, ENTER: 13, BACK: 10009, ESC: 27, BACKSPACE: 8 };
  var QUIET_AFTER_MS = 8 * 1000;        /* lock: the up button dims */
  var BACK_TO_LOCK_MS = 20 * 1000;      /* apps: return to the lock screen when left alone */
  var DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

  var onTV = typeof window.tizen !== 'undefined' && !!window.tizen.application;
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var $ = function (id) { return document.getElementById(id); };
  var body = document.body;
  var stage = $('stage');
  var wall = $('wall');
  var bento = $('bento');
  var tiles = [];
  var rects = [];          /* tile boxes within the bento, measured once */

  var state = { mode: 'lock', focus: 0 };
  var shown = { focus: -1 };
  try {
    var saved = JSON.parse(localStorage.getItem('home.focus2') || 'null');
    if (typeof saved === 'number') state.focus = Math.max(0, Math.min(saved, APPS.length - 1));
  } catch (e) { /* first run */ }

  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  /* ---------- build ---------- */

  function buildTiles() {
    APPS.forEach(function (app, i) {
      var t = document.createElement('div');
      t.className = 'tile';
      t.style.gridArea = app.area;
      t.setAttribute('role', 'listitem');
      t.setAttribute('aria-label', app.title);
      t.innerHTML = '<span class="card"><span class="glow"></span><img src="icons/' + app.hubId + '.png" width="304" height="304" alt=""><span class="t-name">' + app.title + '</span></span>';
      t.querySelector('.glow').style.background =
        'radial-gradient(110% 110% at 100% 100%, ' + rgba(app.tint, 0.42) + ' 0%, ' + rgba(app.tint, 0.12) + ' 45%, ' + rgba(app.tint, 0) + ' 75%)';
      t.addEventListener('click', function () {
        if (!ready) return;
        state.focus = i; showApps(); open(app, t);
      });
      bento.appendChild(t);
      tiles.push(t);
    });
  }

  /* Glass shows the pre-blurred scene; point each surface at the patch of scene behind it
     (stage coordinates). sy compensates for a vertical scale on the element (the tall clock). */
  function alignFrost(el, x, y, sy) {
    sy = sy || 1;
    el.style.setProperty('--bx', -Math.round(x) + 'px');
    el.style.setProperty('--by', -Math.round(y / sy) + 'px');
    if (sy !== 1) el.style.setProperty('--bh', Math.round(1080 / sy) + 'px');
  }

  /* Size classes come from the grid itself, so changing the layout in CSS just works. */
  function measure() {
    var bx = bento.offsetLeft, by = bento.offsetTop;
    rects = tiles.map(function (t) {
      var r = { left: t.offsetLeft, top: t.offsetTop, width: t.offsetWidth, height: t.offsetHeight };
      r.right = r.left + r.width; r.bottom = r.top + r.height;
      r.cx = r.left + r.width / 2; r.cy = r.top + r.height / 2;
      t.classList.add(r.width > 300 ? (r.height > 300 ? 's2x2' : 's2x1') : 's1x1');
      alignFrost(t.querySelector('.card'), bx + r.left, by + r.top);
      return r;
    });
    var w = $('widget');
    alignFrost(w, bx + w.offsetLeft, by + w.offsetTop);
    var up = $('up');
    alignFrost(up, up.offsetLeft, up.offsetTop);
  }

  /* ---------- glass clock, painted into a canvas once per minute ---------- */

  var CLOCK = { top: 104, size: 380, track: -0.055, stretch: 1.24, baseline: 335, digitH: 266 };
  var frostLight = new Image();
  frostLight.src = 'media/frost-light.jpg';
  frostLight.onload = function () { if (ready) drawClock(shownTime); };
  var shownTime = '';

  /* A canvas doesn't trigger a web font download, and nothing else on the page uses Bold, so load
     it explicitly; until then the canvas would silently fall back to a serif. */
  var clockFontReady = false;
  if (document.fonts && document.fonts.load) {
    document.fonts.load('700 ' + CLOCK.size + 'px "SF Display"').then(function () {
      clockFontReady = true;
      if (ready) drawClock(shownTime);
    });
  } else { clockFontReady = true; }

  function drawClock(t) {
    if (!clockFontReady || !t) return;
    var c = $('clockCanvas'), g = c.getContext('2d');
    var W = c.width, H = c.height;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalCompositeOperation = 'source-over';
    g.clearRect(0, 0, W, H);

    /* 1. the digits, tightly tracked and stretched tall */
    g.font = '700 ' + CLOCK.size + 'px "SF Display"';
    g.textBaseline = 'alphabetic';
    var track = CLOCK.track * CLOCK.size, widths = [], total = 0, i;
    for (i = 0; i < t.length; i++) { widths[i] = g.measureText(t[i]).width; total += widths[i]; }
    total += track * (t.length - 1);
    var x = (W - total) / 2;
    g.setTransform(1, 0, 0, CLOCK.stretch, 0, 0);
    g.fillStyle = '#fff';
    for (i = 0; i < t.length; i++) { g.fillText(t[i], x, CLOCK.baseline); x += widths[i] + track; }
    g.setTransform(1, 0, 0, 1, 0, 0);

    var top = (CLOCK.baseline - CLOCK.digitH) * CLOCK.stretch, bottom = CLOCK.baseline * CLOCK.stretch;

    /* 2. frosted fill: the pre-blurred scene, exactly the patch that sits behind the digits */
    g.globalCompositeOperation = 'source-in';
    if (frostLight.complete && frostLight.naturalWidth) g.drawImage(frostLight, 0, -CLOCK.top, 1920, 1080);
    else { g.fillStyle = 'rgba(200,214,204,0.5)'; g.fillRect(0, 0, W, H); }

    /* 3. light veil and 4. top highlight, both only on the digits */
    g.globalCompositeOperation = 'source-atop';
    var veil = g.createLinearGradient(0, top, 0, bottom);
    veil.addColorStop(0, 'rgba(255,255,255,0.20)'); veil.addColorStop(0.5, 'rgba(255,255,255,0.03)'); veil.addColorStop(1, 'rgba(255,255,255,0.12)');
    g.fillStyle = veil; g.fillRect(0, 0, W, H);
    var hi = g.createLinearGradient(0, top, 0, bottom);
    hi.addColorStop(0, 'rgba(255,255,255,0.85)'); hi.addColorStop(0.16, 'rgba(255,255,255,0.22)'); hi.addColorStop(0.38, 'rgba(255,255,255,0)');
    hi.addColorStop(0.78, 'rgba(255,255,255,0)'); hi.addColorStop(1, 'rgba(255,255,255,0.26)');
    g.fillStyle = hi; g.fillRect(0, 0, W, H);
    g.globalCompositeOperation = 'source-over';
  }

  /* ---------- spatial navigation: nearest tile in the pressed direction ---------- */

  function gapOf(lo1, hi1, lo2, hi2) { return lo2 >= hi1 ? lo2 - hi1 : lo1 >= hi2 ? lo1 - hi2 : 0; }

  function neighbour(from, dx, dy) {
    var a = rects[from], best = -1, bestScore = Infinity;
    for (var i = 0; i < rects.length; i++) {
      if (i === from) continue;
      var b = rects[i], main, side;
      if (dx > 0) { if (b.left < a.right - 1) continue; main = b.left - a.right; side = gapOf(a.top, a.bottom, b.top, b.bottom); }
      else if (dx < 0) { if (b.right > a.left + 1) continue; main = a.left - b.right; side = gapOf(a.top, a.bottom, b.top, b.bottom); }
      else if (dy > 0) { if (b.top < a.bottom - 1) continue; main = b.top - a.bottom; side = gapOf(a.left, a.right, b.left, b.right); }
      else { if (b.bottom > a.top + 1) continue; main = a.top - b.bottom; side = gapOf(a.left, a.right, b.left, b.right); }
      if (dx && side > 0) continue;   /* left/right stay within the same row band; no diagonal jumps */
      var centre = dx ? Math.abs(b.cy - a.cy) : Math.abs(b.cx - a.cx);
      var score = main + side * 3 + centre * 0.05;
      if (score < bestScore) { bestScore = score; best = i; }
    }
    return best;
  }

  /* ---------- render: touch only what changed ---------- */

  function render() {
    if (shown.focus !== state.focus) {
      if (shown.focus >= 0) tiles[shown.focus].classList.remove('is-focused');
      tiles[state.focus].classList.add('is-focused');
      shown.focus = state.focus;
    }
    saveFocus();
  }

  var saveTimer;
  function saveFocus() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try { localStorage.setItem('home.focus2', JSON.stringify(state.focus)); } catch (e) { /* storage off */ }
    }, 1500);
  }

  /* ---------- modes ---------- */

  function setMode(mode) {
    if (state.mode === mode) return;
    state.mode = mode;
    body.classList.toggle('apps', mode === 'apps');
    body.classList.toggle('lock', mode === 'lock');
    body.classList.remove('quiet');
    armTimers();
  }
  function showApps() { setMode('apps'); render(); }
  function showLock() { setMode('lock'); }

  var quietTimer, lockTimer;
  function armTimers() {
    clearTimeout(quietTimer); clearTimeout(lockTimer);
    if (state.mode === 'lock') quietTimer = setTimeout(function () { body.classList.add('quiet'); }, QUIET_AFTER_MS);
    else lockTimer = setTimeout(showLock, BACK_TO_LOCK_MS);
  }

  /* ---------- open apps ---------- */

  function press(el) {
    el.classList.add('is-pressed');
    setTimeout(function () { el.classList.remove('is-pressed'); }, 120);
  }

  function open(app, tile) {
    press(tile);
    if (!onTV) { toast('Preview: on the TV this opens ' + app.title + '.'); return; }
    try {
      tizen.application.launch(app.tizenId, null, function (err) {
        if (err && err.name === 'NotFoundError') toast(app.title + ' isn’t installed anymore. Reinstall it from Smart Hub, or remove it from apps.js.');
        else toast('Couldn’t open ' + app.title + '. Try again, or open it from Smart Hub.');
      });
    } catch (e) {
      toast('Couldn’t open ' + app.title + '. Try again, or open it from Smart Hub.');
    }
  }

  var toastTimer;
  function toast(msg) {
    var el = $('toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove('show'); }, 4200);
  }

  /* ---------- remote ---------- */

  function hideApp() {
    if (onTV) { try { tizen.application.getCurrentApplication().hide(); } catch (e) { /* stay */ } }
  }

  var ready = false;   /* geometry is measured once fonts load; ignore keys until then */
  document.addEventListener('keydown', function (e) {
    if (!ready) return;
    var k = e.keyCode;
    var isBack = k === KEY.BACK || k === KEY.ESC || k === KEY.BACKSPACE;
    armTimers();

    if (state.mode === 'lock') {
      body.classList.remove('quiet');
      if (k === KEY.UP || k === KEY.ENTER) { press($('up')); showApps(); }
      else if (isBack) hideApp();
      else if (k !== KEY.LEFT && k !== KEY.RIGHT && k !== KEY.DOWN) return;
      e.preventDefault();
      return;
    }

    if (isBack) { showLock(); e.preventDefault(); return; }
    if (k === KEY.ENTER) { open(APPS[state.focus], tiles[state.focus]); e.preventDefault(); return; }
    var dx = k === KEY.RIGHT ? 1 : k === KEY.LEFT ? -1 : 0;
    var dy = k === KEY.DOWN ? 1 : k === KEY.UP ? -1 : 0;
    if (!dx && !dy) return;
    var n = neighbour(state.focus, dx, dy);
    if (n >= 0) { state.focus = n; render(); }
    e.preventDefault();
  });

  $('up').addEventListener('click', function () { if (ready) showApps(); });
  document.addEventListener('mousemove', function () { if (ready && state.mode === 'lock') { body.classList.remove('quiet'); armTimers(); } });

  /* ---------- wallpaper: plays only while Home is on screen ---------- */

  /* Measured on the TV: right after another app closes, the hardware decoder can still be busy
     and the first play fails ("no supported sources"). So retry with backoff; the poster sits
     behind the video the whole time, so there is never a black screen. */
  var wallTries = 0, wallRetry = 0, WALL_DELAYS = [400, 1200, 3000, 6000];
  function playWall() {
    clearTimeout(wallRetry);
    if (reduceMotion) { wall.pause(); return; }
    if (wall.error) wall.load();
    var p = wall.play();
    if (p && p.then) p.then(function () { wallTries = 0; }, retryWall);
  }
  function retryWall() {
    clearTimeout(wallRetry);
    if (document.hidden || reduceMotion) return;
    var d = wallTries < WALL_DELAYS.length ? WALL_DELAYS[wallTries] : 15000;
    wallTries++;
    wallRetry = setTimeout(playWall, d);
  }
  wall.addEventListener('error', retryWall);
  window.homeStatus = function () { return { playing: !wall.paused && !wall.error, retries: wallTries, t: wall.currentTime, mode: state.mode }; };

  /* ---------- clock + calendar: repaint only when the minute (or day) changes ---------- */

  var lastMinute = -1, lastDay = -1;
  function renderClock() {
    var d = new Date(), h = d.getHours(), m = d.getMinutes();
    var stamp = h * 60 + m;
    if (stamp === lastMinute) return;
    lastMinute = stamp;
    var t = (h % 12 || 12) + ':' + (m < 10 ? '0' : '') + m;
    shownTime = t;
    $('clockText').textContent = t;
    $('wTime').textContent = t;
    if (ready) drawClock(t);
    if (d.getDate() !== lastDay) { lastDay = d.getDate(); renderDate(d); }
  }

  function renderDate(d) {
    $('lockDate').textContent = DAYS[d.getDay()] + ' ' + d.getDate() + ' ' + MONTHS[d.getMonth()];
    $('wDay').textContent = DAYS[d.getDay()];
    $('wDate').textContent = d.getDate() + ' ' + MONTHS[d.getMonth()];
    $('wMonth').textContent = MONTHS[d.getMonth()];
    var html = '';
    ['M', 'T', 'W', 'T', 'F', 'S', 'S'].forEach(function (w) { html += '<span class="wd">' + w + '</span>'; });
    var first = new Date(d.getFullYear(), d.getMonth(), 1);
    var lead = (first.getDay() + 6) % 7;                 /* weeks start on Monday */
    var count = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    for (var i = 0; i < lead; i++) html += '<span></span>';
    for (var day = 1; day <= count; day++) html += '<span class="d' + (day === d.getDate() ? ' today' : '') + '">' + day + '</span>';
    $('wGrid').innerHTML = html;
  }

  /* ---------- fit (desktop preview only; the TV is exactly 1920x1080) ---------- */

  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    stage.style.transform = 'translate(-50%, -50%) scale(' + s + ')';
  }

  /* ---------- start ---------- */

  buildTiles();
  fit();
  window.addEventListener('resize', fit);
  renderClock();
  setInterval(renderClock, 5000);
  playWall();

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      clearTimeout(wallRetry);
      wall.pause();   /* give the video decoder back to whatever app is on screen */
    } else {
      wallTries = 0;
      lastMinute = -1; renderClock(); playWall();
      if (ready) armTimers();
    }
  });

  /* Wait for fonts so the grid is measured with its final metrics. */
  var start = function () {
    measure(); render(); ready = true; armTimers();
    drawClock(shownTime);
  };
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(start); else window.addEventListener('load', start);
})();
