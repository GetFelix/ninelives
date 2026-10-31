// Home hero: records drifting along the logo's 9 (bowl and tail). Fades out one now and then.
(function () {
  "use strict";
  var canvas = document.querySelector(".hero-bg");
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  var root = document.documentElement;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)");
  var BAND = 33; // ~30 fps
  var SP = 12; // spacing between records, in logo units
  var SPEED = 7; // logo units per second
  var pts = [], cum = [], total = 0;
  var dots = [], phase = 0;
  var w = 0, h = 0, dpr = 1, k = 1, ox = 0, oy = 0;
  var col = {}, running = false, visible = true, last = 0, lastDraw = 0, nextLoss = 0, clock = 0;

  // Same geometry as scripts/brand/mark.py: the tail's cubic, then the bowl.
  (function () {
    var CX = 124, CY = 100, R = 54;
    var p = [[CX - 70, CY + 112], [CX - 20, CY + 150], [CX + R, CY + 92], [CX + R, CY]];
    var i, t, u;
    for (i = 0; i <= 90; i++) {
      t = i / 90; u = 1 - t;
      pts.push([
        u * u * u * p[0][0] + 3 * u * u * t * p[1][0] + 3 * u * t * t * p[2][0] + t * t * t * p[3][0],
        u * u * u * p[0][1] + 3 * u * u * t * p[1][1] + 3 * u * t * t * p[2][1] + t * t * t * p[3][1]
      ]);
    }
    for (i = 1; i <= 150; i++) {
      var a = -(i / 150) * Math.PI * 1.85;
      pts.push([CX + R * Math.cos(a), CY + R * Math.sin(a)]);
    }
    cum.push(0);
    for (i = 1; i < pts.length; i++) {
      total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      cum.push(total);
    }
  })();

  function at(s) {
    var lo = 0, hi = cum.length - 1;
    while (hi - lo > 1) {
      var m = (lo + hi) >> 1;
      if (cum[m] <= s) lo = m; else hi = m;
    }
    var f = (s - cum[lo]) / (cum[hi] - cum[lo] || 1);
    return [pts[lo][0] + (pts[hi][0] - pts[lo][0]) * f, pts[lo][1] + (pts[hi][1] - pts[lo][1]) * f];
  }
  function readColors() {
    var cs = getComputedStyle(root);
    col.dot = cs.getPropertyValue("--record").trim();
    col.head = cs.getPropertyValue("--amber").trim();
  }
  function layout() {
    var r = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = r.width; h = r.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // The 9 is ~215 units tall, right of the headline, smaller on phones.
    var small = w < 700;
    k = small ? Math.min(h * 0.5, w * 0.8) / 215 : Math.min(h * 1.05, w * 0.55) / 215;
    ox = (small ? w * 0.62 : w * 0.78) - 124 * k;
    oy = (small ? h * 0.3 : h * 0.5) - 150 * k;
  }
  function seed() {
    dots = [];
    var n = Math.ceil(total / SP) + 2;
    for (var i = 0; i < n; i++) dots.push({ a: 1 });
    phase = 0;
  }
  function draw() {
    ctx.clearRect(0, 0, w, h);
    var s = total + phase, r = Math.max(1.4, 2.6 * k);
    for (var i = 0; i < dots.length; i++) {
      var d = dots[i];
      if (i > 0) s -= SP * (dots[i - 1].a + d.a) / 2;
      if (s < -SP) break;
      var e = Math.min(1, Math.max(0, (s + SP) / (SP * 2)), Math.max(0, (total + SP - s) / SP));
      var alpha = e * d.a;
      if (alpha <= 0.01) continue;
      var p = at(Math.min(Math.max(s, 0), total));
      var head = i === 1;
      ctx.globalAlpha = (head ? 0.5 : 0.2) * alpha;
      ctx.fillStyle = head ? col.head : col.dot;
      ctx.beginPath();
      ctx.arc(ox + p[0] * k, oy + p[1] * k, head ? r * 1.35 : r, 0, 6.2832);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  function advance(dt) {
    clock += dt;
    phase += SPEED * dt;
    while (phase >= SP) {
      phase -= SP;
      dots.shift();
      dots.push({ a: 1 });
    }
    for (var i = dots.length - 1; i >= 0; i--) {
      if (!dots[i].dying) continue;
      dots[i].a -= dt / 1.8;
      if (dots[i].a <= 0) dots.splice(i, 1);
    }
    if (clock >= nextLoss) {
      nextLoss = clock + 6 + Math.random() * 4;
      var j = 3 + Math.floor(Math.random() * (dots.length - 12));
      if (dots[j] && !dots[j].dying) dots[j].dying = true;
    }
    while (dots.length < Math.ceil(total / SP) + 2) dots.push({ a: 1 });
  }
  function step(t) {
    if (!running) return;
    requestAnimationFrame(step);
    if (t - lastDraw < BAND) return;
    advance(last ? Math.min((t - last) / 1000, 0.1) : 0);
    last = lastDraw = t;
    draw();
  }
  function start() {
    if (running || reduce.matches || !visible || document.hidden) return;
    running = true; last = 0;
    requestAnimationFrame(step);
  }
  function stop() { running = false; }
  function redraw() { readColors(); draw(); }

  readColors(); layout(); seed(); draw(); start();
  var rt;
  addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(function () { layout(); draw(); }, 150);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
  new MutationObserver(redraw).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  reduce.addEventListener("change", function () { reduce.matches ? stop() : start(); });
  document.addEventListener("visibilitychange", function () { document.hidden ? stop() : start(); });
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (en) {
      visible = en[0].isIntersecting;
      visible ? start() : stop();
    }).observe(canvas);
  }
})();
