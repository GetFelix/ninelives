+++
title = "Get involved"
description = "NineLives is built in the open. Here is how to join in."
template = "page.html"

[extra]
kicker = "Get involved"
+++

Nothing boots yet, so the most useful help right now is opinions and review.

## Talk it through

Design questions go in [Discussions](https://github.com/GetFelix/ninelives/discussions). The first one asks whether to use [Linux or a kernel of our own](https://github.com/GetFelix/ninelives/discussions/22). Nothing is decided, so this is the time to weigh in.

## Pick up an issue

Every milestone is split into [issues](https://github.com/GetFelix/ninelives/issues). The two design ones are good places to start: [our own PID 1 or systemd](https://github.com/GetFelix/ninelives/issues/15) and [how a machine proves its identity](https://github.com/GetFelix/ninelives/issues/16).

## Build it

Clone with submodules, since felixd is pinned as one:

```sh
git clone --recurse-submodules https://github.com/GetFelix/ninelives
```

There is no build yet. The proposal is to build images with [mkosi](https://github.com/systemd/mkosi) and boot them in qemu in CI, and how images are built is still [open](https://github.com/GetFelix/ninelives/issues/1). The first milestone adds a workflow that builds the image and boots it on every pull request, and you will be able to read its log without installing anything.

## Change this site

The site is Zola, in `site/`. Milestone status is one file, `site/data/milestones.toml`. Blog posts are Markdown in `site/content/blog/`. The design pages are rendered from `docs/`, so fix the docs and the site follows.

```sh
site/scripts/build.sh
```

builds it into `site/public/`. You need [Zola](https://www.getzola.org/) and Python 3.
