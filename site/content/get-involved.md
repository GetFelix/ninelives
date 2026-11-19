+++
title = "Get involved"
description = "NineLives is built in the open. Here is how to join in."
template = "page.html"

[extra]
kicker = "Get involved"
+++

Nothing boots yet, so the most useful help right now is review: of the decisions, of the designs as they are written, and soon of the first code.

## Talk it through

Design questions go in [Discussions](https://github.com/GetFelix/ninelives/discussions). The choices for the first three milestones are written up as [decision records](https://github.com/GetFelix/ninelives/tree/main/docs/decisions), each with the options turned down and what would make us decide again. If you think one is wrong, say why there.

## Pick up an issue

Every milestone is split into [issues](https://github.com/GetFelix/ninelives/issues). Two design ones are good places to start: [the init's design](https://github.com/GetFelix/ninelives/issues/46) and [how a machine proves its identity](https://github.com/GetFelix/ninelives/issues/16). How work moves from design to code is in [CONTRIBUTING.md](https://github.com/GetFelix/ninelives/blob/main/CONTRIBUTING.md).

## Build it

```sh
git clone https://github.com/GetFelix/ninelives
```

There is no build yet. Images will be built with [mkosi](https://github.com/systemd/mkosi) in CI and booted in qemu on every pull request, so you can read a boot log without installing anything. Felix and felixd are not built here: the image takes their signed releases, pinned in `images/components.toml`.

## Change this site

The site is Zola, in `site/`. Milestone status is one file, `site/data/milestones.toml`. Blog posts are Markdown in `site/content/blog/`. The design pages are rendered from `docs/`, so fix the docs and the site follows.

```sh
site/scripts/build.sh
```

builds it into `site/public/`. You need [Zola](https://www.getzola.org/) and Python 3.
