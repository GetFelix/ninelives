// Home hero: streams of records drifting across the page. Every so often one stream dies and
// its in-flight records curve over into a neighbour, then a new stream starts elsewhere.
(function () {
  "use strict";
  var hero = document.querySelector(".hero");
  var canvas = document.querySelector(".hero-bg");
  if (!hero || !canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  var root = document.documentElement;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)");
  var TAU = 6.2832, SPAN = 150, LEAD = 4;
  var streams = [], trains = [], boxes = [], land = null, floorY = 0;
  var w = 0, h = 0, gap = 18, col = {}, clock = 0, nextKill = 6;
  var running = false, visible = true, last = 0, lastDraw = 0;

  function rnd(a, b) { return a + Math.random() * (b - a); }
  function ease(x) { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); }
  function readColors() {
    var cs = getComputedStyle(root);
    col.dot = cs.getPropertyValue("--record").trim();
    col.head = cs.getPropertyValue("--amber").trim();
  }
  function rectOf(els) {
    var c = canvas.getBoundingClientRect(), x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    els.forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (!r.width) return;
      x0 = Math.min(x0, r.left); y0 = Math.min(y0, r.top);
      x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom);
    });
    if (x1 < x0) return null;
    return { x: (x0 + x1) / 2 - c.left, y: (y0 + y1) / 2 - c.top, hw: (x1 - x0) / 2 + 36, hh: (y1 - y0) / 2 + 28 };
  }
  function layout() {
    var r = canvas.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = r.width; h = r.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    gap = w < 700 ? 15 : 18;
    // Quieter behind the headline block and behind the logo.
    var c = canvas.getBoundingClientRect(), strip = hero.querySelector(".strip ol"), slot = hero.querySelector(".strip li:last-child i");
    land = null;
    if (slot && slot.getBoundingClientRect().width) {
      var q = slot.getBoundingClientRect();
      land = { x: q.left + q.width / 2 - c.left, y: q.top + q.height / 2 - c.top };
    }
    floorY = Math.min(h - 8, Math.max(strip ? strip.getBoundingClientRect().bottom - c.top + 10 : h, h - 22));
    boxes = [rectOf([].slice.call(hero.querySelectorAll(".hero-tag, h1, .lede, .actions"))), rectOf([].slice.call(hero.querySelectorAll(".hero-mark")))].filter(Boolean);
  }
  function quiet(x, y) {
    var f = 1;
    for (var i = 0; i < boxes.length; i++) {
      var b = boxes[i], d = Math.hypot((x - b.x) / b.hw, (y - b.y) / b.hh);
      f = Math.min(f, 0.12 + 0.88 * ease((d - 0.7) / 0.8));
    }
    return f;
  }
  // Roles: the last stream runs low across the bottom, the one before it lands in the log strip.
  function fresh(s, i, n) {
    s.y = (i + rnd(0.2, 0.8)) / (n - 2); s.amp = rnd(0.06, 0.11); s.f = rnd(0.45, 0.9);
    s.ph = rnd(0, TAU); s.v = rnd(11, 19); s.dead = 0; s.role = i === n - 1 ? 2 : i === n - 2 ? 1 : 0;
    if (s.role === 1) { s.y = 0.5; s.amp = 0.07; }
    if (s.role === 2) { s.amp = 0.006; s.f = 1; }
  }
  function endX(s) { return s.role === 1 && land ? land.x : w; }
  function addTrains(si, fade) {
    for (var j = 0; j < 3; j++) { var n = Math.round(rnd(14, 24)); trains.push({ s: si, to: -1, kx: 0, kt: 0, x: rnd(0, endX(streams[si])), n: n, nmax: n, g: 0, a: fade ? 0 : 1 }); }
  }
  function yAt(s, x) {
    if (s.role === 2) return floorY + s.amp * h * Math.sin(x / w * TAU * 2 + clock * 0.2);
    var y = h * (s.y + s.amp * Math.sin((x / w) * TAU * s.f + s.ph + clock * 0.05));
    // The landing stream bends into the strip's newest slot.
    if (s.role === 1 && land) y += (land.y - y) * ease((x - (land.x - 260)) / 260);
    return y;
  }
  function seed() {
    var n = w < 700 ? 5 : 7; // the last two are the landing and floor streams
    streams = []; trains = [];
    for (var i = 0; i < n; i++) { var s = {}; fresh(s, i, n); streams.push(s); addTrains(i, false); }
  }
  function nearest(i) {
    var best = -1, bd = 9;
    streams.forEach(function (s, k) {
      if (k !== i && !s.dead && !s.role && Math.abs(s.y - streams[i].y) < bd) { bd = Math.abs(s.y - streams[i].y); best = k; }
    });
    return best;
  }
  function advance(dt) {
    clock += dt;
    trains.forEach(function (t) {
      var s = streams[t.s], v = s.v, xe = endX(s);
      if (t.to >= 0) {
        v += (streams[t.to].v - v) * ease((t.x - t.kx) / SPAN);
        // Only the lead records carry over; the rest of the trail has faded by then.
        if (t.x - t.kx >= SPAN + LEAD * gap) { t.s = t.to; t.to = -1; t.n = LEAD; }
      } else if (t.n < t.nmax) {
        t.n = Math.min(t.nmax, t.n + dt / 0.9);
      }
      t.x += v * dt;
      if (t.x - t.n * gap > xe + 20) { t.x -= xe + 40 + t.n * gap; if (t.to >= 0) t.kx -= xe + 40 + t.n * gap; }
      t.a = Math.min(1, t.a + dt / 1.5);
    });
    if (clock >= nextKill) {
      nextKill = clock + rnd(8, 12);
      var live = streams.map(function (s, i) { return i; }).filter(function (i) { return !streams[i].dead && !streams[i].role; });
      if (live.length > 3) {
        var k = live[Math.floor(Math.random() * live.length)], to = nearest(k);
        streams[k].dead = clock; streams[k].rs = rnd(3, 5);
        trains.forEach(function (t) { if (t.s === k && t.to < 0) { t.to = to; t.kx = t.x; t.kt = clock; } });
      }
    }
    streams.forEach(function (s, i) {
      if (s.dead && clock - s.dead > s.rs && !trains.some(function (t) { return t.s === i; })) {
        fresh(s, i, streams.length);
        addTrains(i, true);
      }
    });
  }
  function dot(x, y, r, a, c, glow) {
    var q = quiet(x, y);
    ctx.globalAlpha = a * q;
    ctx.fillStyle = c;
    if (glow) { ctx.shadowColor = c; ctx.shadowBlur = 7; }
    ctx.beginPath();
    ctx.arc(x, y, r, 0, TAU);
    ctx.fill();
    if (glow) ctx.shadowBlur = 0;
  }
  function draw() {
    ctx.clearRect(0, 0, w, h);
    trains.forEach(function (t) {
      var s = streams[t.s], nb = t.to >= 0 ? streams[t.to] : null, xe = endX(s);
      var n = Math.floor(t.n), seen = 0, head = null, j;
      for (j = 0; j < n; j++) {
        var x = t.x - j * gap;
        if (x < -10 || x > xe + 10) continue;
        var y = yAt(s, x), f = t.a;
        if (nb) {
          var m = ease((t.x - t.kx - j * gap) / SPAN);
          y += (yAt(nb, x) - y) * m;
          f *= 1 - 0.4 * Math.sin(Math.PI * m);
          if (j >= LEAD) f *= Math.max(0, 1 - (clock - t.kt));
        }
        f *= Math.min(1, x / 60, (xe - x) / 60);
        if (f <= 0.01) continue;
        if (j === 0) { head = [x, y, f]; continue; }
        if (j < 6) seen += f;
        dot(x, y, 1.5 + 0.9 * (1 - j / n), 0.26 * f * (1 - 0.5 * j / n), col.dot, false);
      }
      // A head is only drawn while a trail is visible behind it.
      if (head) dot(head[0], head[1], 3, 0.75 * head[2] * Math.min(1, seen / 2.5), col.head, true);
    });
    ctx.globalAlpha = 1;
  }
  function step(t) {
    if (!running) return;
    requestAnimationFrame(step);
    if (t - lastDraw < 33) return; // ~30 fps
    advance(last ? Math.min((t - last) / 1000, 0.1) : 0);
    last = lastDraw = t;
    draw();
  }
  function start() {
    if (running || reduce.matches || !visible || document.hidden) return;
    running = true; last = 0;
    requestAnimationFrame(step);
  }
  function redraw() { readColors(); draw(); }

  readColors(); layout(); seed(); draw(); start();
  addEventListener("load", function () { layout(); draw(); });
  var rt;
  addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(function () { layout(); draw(); }, 150);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
  new MutationObserver(redraw).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  reduce.addEventListener("change", function () { reduce.matches ? (running = false) : start(); });
  document.addEventListener("visibilitychange", function () { document.hidden ? (running = false) : start(); });
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (en) {
      visible = en[0].isIntersecting;
      visible ? start() : (running = false);
    }).observe(canvas);
  }
})();
