# ninelives.getfelix.dev

The site for NineLives, built with [Zola](https://www.getzola.org/) 0.23.

| Path | What it is |
| --- | --- |
| `data/milestones.toml` | Milestone status and the open questions, read by the home page, the roadmap and the design index. The one file to edit when status changes or a question is decided. |
| `content/devlog/` | One Markdown file per post. |
| `content/design/` | The design section's intro. `os.md` and `topology.md` are generated from `docs/` by `scripts/gen_docs.py` and are git-ignored. |
| `templates/`, `static/css/` | The page shell, pages and the stylesheet. `static/css/peek.css` is the peeking cat in the header that links to getfelix.dev; getfelix.dev has an identical copy at `static/assets/peek.css`, so change both together. |
| `static/vendor/` | `mermaid-11.4.1.min.js`, the unmodified `dist/mermaid.min.js` from the mermaid 11.4.1 npm package (MIT). sha256 `a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e`. |
| `static/` | Copied as is: fonts (Fraunces, Hanken Grotesk, IBM Plex Mono, all SIL OFL), the brand SVGs, `CNAME`. |

```sh
site/scripts/build.sh     # renders docs/ and builds into site/public/
python3 site/scripts/check_links.py site/public
```

For a live preview run `python3 site/scripts/gen_docs.py`, then `zola serve` in `site/`.
