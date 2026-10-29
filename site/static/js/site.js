// Theme toggle. The page follows the system until a choice is stored.
(function () {
  var root = document.documentElement;
  var btn = document.querySelector(".theme");
  if (!btn) return;
  btn.addEventListener("click", function () {
    var cur = root.getAttribute("data-theme");
    if (!cur) cur = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    var next = cur === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) {}
  });
})();
