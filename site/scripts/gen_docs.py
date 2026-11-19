#!/usr/bin/env python3
"""Render the repository's docs/ as site pages, so the design has one source.

Writes site/content/design/{os,topology,inference-as-effect,felixd-contract}.md
(git-ignored). Run by build.sh. Links to other files in the repository, such as
the decision records, point at GitHub.
"""
import pathlib
import posixpath
import re

root = pathlib.Path(__file__).resolve().parents[2]
out = root / "site" / "content" / "design"
docs = [
    # source, page, weight, description
    ("design.md", "os", 1, "What boots on each machine, how it joins, and which services live on the log."),
    ("topology.md", "topology", 2, "Where Felix and felixd run, and how a cluster comes up."),
    ("inference-as-effect.md", "inference-as-effect", 3, "How model inference runs as a recorded effect so its decisions can be replayed."),
    ("felixd-contract.md", "felixd-contract", 4, "Every property of felixd the image depends on, for the pinned release."),
]
links = {src: f"/design/{page}/" for src, page, _, _ in docs}
repo = "https://github.com/GetFelix/ninelives"


def to_github(m):
    target, anchor = m.group(1), m.group(2) or ""
    path = posixpath.normpath(posixpath.join("docs", target))
    if path.startswith("docs/") and path[5:] in links:
        return f"]({links[path[5:]]}{anchor})"
    kind = "tree" if target.endswith("/") or (root / path).is_dir() else "blob"
    return f"]({repo}/{kind}/main/{path}{anchor})"


for src, page, weight, desc in docs:
    text = (root / "docs" / src).read_text(encoding="utf-8")
    m = re.match(r"# (.+)\n", text)
    title = m.group(1)
    body = text[m.end():].lstrip("\n")
    for name, url in links.items():
        body = re.sub(r"\]\(%s(#[^)]*)?\)" % re.escape(name), lambda k: "](%s%s)" % (url, k.group(1) or ""), body)
    # Other relative links, including ./ and ../ spellings of the pages above.
    body = re.sub(r"\]\((?!https?://|mailto:|#|/)([^)#\s]+)(#[^)]*)?\)", to_github, body)
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
