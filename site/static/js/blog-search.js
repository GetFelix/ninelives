/* Search on the blog index. The index is Zola's elasticlunr file; it and the
   library load on first use so the page itself stays light. */
(function () {
  var box = document.getElementById("blog-search");
  var form = document.getElementById("search-form");
  var input = document.getElementById("search-q");
  var status = document.getElementById("search-status");
  var results = document.getElementById("search-results");
  var list = document.getElementById("post-list");
  if (!box || !form || !input || !status || !results || !list) return;
  var pager = document.querySelector(".pager");
  var topics = document.querySelector(".tag-nav");
  box.hidden = false;

  var index = null;
  var loading = null;

  function load(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error(src)); };
      document.head.appendChild(s);
    });
  }

  function ready() {
    if (index) return Promise.resolve(index);
    if (!loading) {
      loading = load("/elasticlunr.min.js")
        .then(function () { return load("/search_index.en.js"); })
        .then(function () {
          index = window.elasticlunr.Index.load(window.searchIndex);
          return index;
        })
        .catch(function (e) { loading = null; throw e; });
    }
    return loading;
  }

  // The index stores absolute URLs with a trailing slash; pages are served without it.
  function pathOf(ref) {
    return ref.replace(/^https?:\/\/[^/]+/, "").replace(/\/$/, "");
  }

  function show(on) {
    list.hidden = on;
    if (pager) pager.hidden = on;
    results.hidden = !on;
  }

  function render(q, hits) {
    results.textContent = "";
    hits.forEach(function (hit) {
      var doc = index.documentStore.getDoc(hit.ref);
      if (!doc || !doc.body || pathOf(hit.ref).indexOf("/blog/") !== 0) return;
      var li = document.createElement("li");
      var h = document.createElement("h2");
      var a = document.createElement("a");
      a.href = pathOf(hit.ref);
      a.textContent = doc.title;
      h.appendChild(a);
      li.appendChild(h);
      if (doc.description) {
        var p = document.createElement("p");
        p.textContent = doc.description;
        li.appendChild(p);
      }
      results.appendChild(li);
    });
    var n = results.children.length;
    status.textContent = n
      ? n + (n === 1 ? " post matches " : " posts match ") + "“" + q + "”."
      : "No posts match “" + q + "”.";
  }

  function run() {
    var q = input.value.trim();
    try {
      var url = new URL(location.href);
      if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
      history.replaceState(null, "", url);
    } catch (e) {}
    if (!q) {
      show(false);
      status.textContent = "";
      return;
    }
    status.textContent = "Searching…";
    ready().then(function () {
      if (input.value.trim() !== q) return;
      var hits = index.search(q, {
        fields: { title: { boost: 3 }, description: { boost: 2 }, body: { boost: 1 } },
        bool: "AND",
        expand: true
      });
      show(true);
      render(q, hits);
    }).catch(function () {
      status.textContent = "Search could not load. Browse by tag instead.";
    });
  }

  input.addEventListener("focus", function () { ready().catch(function () {}); }, { once: true });
  input.addEventListener("input", run);
  form.addEventListener("submit", function (e) { e.preventDefault(); run(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && input.value) {
      input.value = "";
      run();
    } else if (e.key === "ArrowDown") {
      var first = results.querySelector("a");
      if (first && !results.hidden) { e.preventDefault(); first.focus(); }
    }
  });
  // Arrow keys move between results; Up from the first returns to the box.
  results.addEventListener("keydown", function (e) {
    var links = Array.prototype.slice.call(results.querySelectorAll("a"));
    var i = links.indexOf(document.activeElement);
    if (i < 0) return;
    if (e.key === "ArrowDown" && i < links.length - 1) { e.preventDefault(); links[i + 1].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); (i ? links[i - 1] : input).focus(); }
    else if (e.key === "Escape") { input.focus(); }
  });

  var q0 = new URLSearchParams(location.search).get("q");
  if (q0) { input.value = q0; run(); }
})();
