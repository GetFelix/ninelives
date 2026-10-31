#!/usr/bin/env python3
"""Render the repository's docs/ as site pages, so the design has one source.

Writes site/content/design/{os,topology,inference-as-effect}.md (git-ignored). Run by build.sh.
"""
import pathlib
import re

root = pathlib.Path(__file__).resolve().parents[2]
out = root / "site" / "content" / "design"
docs = [
    # source, page, weight, description
    ("design.md", "os", 1, "What boots on each machine, how it joins, and which services live on the log."),
    ("topology.md", "topology", 2, "Where Felix and felixd run, and how a cluster comes up."),
    ("inference-as-effect.md", "inference-as-effect", 3, "How model inference runs as a recorded effect so its decisions can be replayed."),
]
links = {"topology.md": "/design/topology/", "design.md": "/design/os/", "inference-as-effect.md": "/design/inference-as-effect/"}

for src, page, weight, desc in docs:
    text = (root / "docs" / src).read_text(encoding="utf-8")
    m = re.match(r"# (.+)\n", text)
    title = m.group(1)
    body = text[m.end():].lstrip("\n")
    for name, url in links.items():
        body = re.sub(r"\]\(%s(#[^)]*)?\)" % re.escape(name), lambda k: "](%s%s)" % (url, k.group(1) or ""), body)
    front = (
        "+++\n"
        f'title = "{title}"\n'
        f'description = "{desc}"\n'
        f"weight = {weight}\n"
        'template = "doc.html"\n'
        f"slug = \"{page}\"\n\n"
        f'[extra]\nsource = "{src}"\n'
        "+++\n\n"
    )
    (out / f"{page}.md").write_text(front + body, encoding="utf-8")
print("rendered docs/ into site/content/design/")
