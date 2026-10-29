# ninelives.getfelix.dev

The site for NineLives, built with [Zola](https://www.getzola.org/) 0.23.

| Path | What it is |
| --- | --- |
| `data/milestones.toml` | Milestone status, read by the home page and the roadmap. The one file to edit when status changes. |
| `content/devlog/` | One Markdown file per post. |
| `content/design/` | The design section's intro. `os.md` and `topology.md` are generated from `docs/` by `scripts/gen_docs.py` and are git-ignored. |
| `templates/`, `static/css/` | The page shell, pages and the stylesheet. |
| `static/` | Copied as is: fonts (Fraunces, Hanken Grotesk, IBM Plex Mono, all SIL OFL), the brand SVGs, `CNAME`. |

```sh
site/scripts/build.sh     # renders docs/ and builds into site/public/
python3 site/scripts/check_links.py site/public
```

For a live preview run `python3 site/scripts/gen_docs.py`, then `zola serve` in `site/`.
