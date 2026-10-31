// Home hero: records drifting around the ring behind the logo. One fades out now and then.
(function () {
  "use strict";
  var canvas = document.querySelector(".hero-bg");
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  var root = document.documentElement;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)");
  var N = 48, LAP = 75, EASE = 1.5; // dots, seconds per lap, seconds to fade or grow
  var dots = [], phase = -Math.PI / 4, clock = 0, nextLoss = 6;
  var w = 0, h = 0, rad = 0, col = {};
  var running = false, visible = true, last = 0, lastDraw = 0;

  function readColors() {
    var cs = getComputedStyle(root);
    col.dot = cs.getPropertyValue("--record").trim();
    col.head = cs.getPropertyValue("--amber").trim();
  }
  function layout() {
    var r = canvas.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = r.width; h = r.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // The ring is the hero mark's ::before, inset 8% on every side.
    rad = Math.min(w, h) * 0.42;
  }
  function draw() {
    ctx.clearRect(0, 0, w, h);
    var sum = 0, i, d;
    for (i = 0; i < dots.length; i++) {
      d = dots[i];
      d.g = i ? (dots[i - 1].a + d.a) / 2 : 0;
      sum += d.g;
    }
    // The gap before the head wraps around from the last dot.
    var wrap = (dots[dots.length - 1].a + dots[0].a) / 2;
    sum += wrap;
    var cum = 0, k = Math.max(1, w / 400);
    for (i = 0; i < dots.length; i++) {
      d = dots[i];
      cum += d.g;
      var ang = phase - (cum / sum) * 6.2832;
      var head = i === 0;
      ctx.globalAlpha = (head ? 0.7 : 0.22) * d.a;
      ctx.fillStyle = head ? col.head : col.dot;
      if (head) { ctx.shadowColor = col.head; ctx.shadowBlur = 8; }
      ctx.beginPath();
      ctx.arc(w / 2 + rad * Math.cos(ang), h / 2 + rad * Math.sin(ang), head ? 3 : 1.5 + 0.5 * Math.min(k, 1), 0, 6.2832);
      ctx.fill();
      ctx.shadowBlur = 0;
    }
    ctx.globalAlpha = 1;
  }
  function advance(dt) {
    clock += dt;
    phase += (6.2832 / LAP) * dt;
    var i;
    for (i = dots.length - 1; i >= 0; i--) {
      var d = dots[i];
      if (d.dying) {
        d.a -= dt / EASE;
        if (d.a <= 0) { dots.splice(i, 1); dots.unshift({ a: 0 }); }
      } else if (d.a < 1) {
        d.a = Math.min(1, d.a + dt / EASE);
      }
    }
    if (clock >= nextLoss) {
      nextLoss = clock + 6 + Math.random() * 4;
      var j = 3 + Math.floor(Math.random() * (dots.length - 4));
      dots[j].dying = true;
    }
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

  for (var n = 0; n < N; n++) dots.push({ a: 1 });
  readColors(); layout(); draw(); start();
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
