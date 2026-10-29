#!/usr/bin/env python3
"""Fail if an internal link or ARIA reference in the built site points at
something that doesn't exist, or an id is repeated on a page.

Usage: check_links.py [built-site-dir] [--external]
--external also requests every https link once and reports the ones that fail.
"""
import re
import sys
import urllib.request
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
ROOT = Path(args[0] if args else Path(__file__).resolve().parents[1] / "public").resolve()
if not (ROOT / "index.html").exists():
    sys.exit("%s has no index.html; build the site first (site/scripts/build.sh)" % ROOT)
pages = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in sorted(ROOT.rglob("*.html"))}
ids, problems, external = {}, [], set()

for name, html in pages.items():
    found = re.findall(r'\bid="([^"]+)"', html)
    ids[name] = set(found)
    for dup in sorted({i for i in found if found.count(i) > 1}):
        problems.append("%s: id %s appears more than once" % (name, dup))

for name, html in pages.items():
    for href in re.findall(r'\b(?:href|src)="([^"]+)"', html):
        href = re.sub(r"^https://ninelives\.getfelix\.dev(?=/|$)", "", href) or "/"
        if href.startswith("https://"):
            external.add(href.split("#")[0])
            continue
        if re.match(r"[a-z]+:", href):
            continue
        path, _, frag = href.partition("#")
        path = path.split("?")[0]
        if path and not path.startswith("/"):
            path = (Path(name).parent / path).as_posix()
        path = path.lstrip("/")
        if path == "" or path.endswith("/"):
            path += "index.html"
        target = name if href.startswith("#") else path
        if not (ROOT / target).exists():
            problems.append("%s: %s points at a missing file" % (name, href))
        elif frag and target in ids and frag not in ids[target]:
            problems.append("%s: %s points at a missing id" % (name, href))
    for attr in ("aria-labelledby", "aria-describedby", "aria-controls", "for"):
        for value in re.findall(r'\b%s="([^"]+)"' % attr, html):
            for ref in value.split():
                if ref not in ids[name]:
                    problems.append("%s: %s names missing id %s" % (name, attr, ref))
    if "<title>" not in html or "<h1" not in html and name != "404.html":
        problems.append("%s: missing title or h1" % name)

if "--external" in sys.argv:
    for url in sorted(external):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ninelives-linkcheck"})
            urllib.request.urlopen(req, timeout=20).close()
        except Exception as e:
            problems.append("external %s: %s" % (url, e))

for p in problems:
    print(p)
if problems:
    sys.exit(1)
print("Every internal link and ARIA reference resolves on %d pages (%d external links)." % (len(pages), len(external)))
