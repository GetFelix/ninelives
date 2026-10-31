// Theme toggle: light, dark, solarized. Follows the system until a choice is stored.
(function () {
  var root = document.documentElement;
  var btn = document.querySelector(".theme");
  var themes = ["light", "dark", "solarized"];
  function current() {
    return root.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function next() { return themes[(themes.indexOf(current()) + 1) % themes.length]; }
  function label() {
    btn.setAttribute("aria-label", "Switch to " + next() + " theme");
    btn.setAttribute("title", "Switch to " + next() + " theme");
  }
  if (!btn) return;
  label();
  btn.addEventListener("click", function () {
    var n = next();
    root.setAttribute("data-theme", n);
    try { localStorage.setItem("theme", n); } catch (e) {}
    label();
  });
})();

// Phone menu.
(function () {
  var btn = document.querySelector(".menu");
  var header = document.querySelector(".site-header");
  if (!btn || !header) return;
  function set(open) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    header.classList.toggle("open", open);
  }
  btn.addEventListener("click", function () { set(btn.getAttribute("aria-expanded") !== "true"); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
})();

// Wide tables scroll inside their own box instead of the page.
document.querySelectorAll(".prose table").forEach(function (t) {
  var w = document.createElement("div");
  w.className = "table-wrap";
  t.parentNode.insertBefore(w, t);
  w.appendChild(t);
});

// Sections fade up as they scroll in. Without IntersectionObserver, show everything.
(function () {
  var els = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    els.forEach(function (el) { el.classList.add("in"); });
    return;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    });
  }, { rootMargin: "0px 0px -8% 0px" });
  els.forEach(function (el) { io.observe(el); });
})();
