#!/bin/sh
# Builds the site into site/public/. Needs Zola (CI pins the version in .github/workflows/site.yml).
set -eu
cd "$(dirname "$0")/.."
python3 scripts/gen_docs.py
zola build "$@"
