#!/usr/bin/env bash
# Build the init inside the pinned bootstrap container (docs/components/image.md,
# "Inputs"): the Rust toolchain and the crates come from pins, not from rustup.
#
#   init-build.sh prepare --work DIR   as root, with network: install the build
#                                      tools from the lock, then fetch and check
#                                      the toolchain archives and the crates
#   init-build.sh build --work DIR     no network needed: build with --locked
#                                      --offline, leaving DIR/out/init
#
# NL_ARCH picks the architecture (x86_64 if unset). The rebuild job runs
# `build` with libfaketime loaded and RUSTC_WRAPPER set, as in ci.yml.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/arch.sh
. "$repo/scripts/arch.sh"

cmd=${1:-}
[[ $cmd == prepare || $cmd == build ]] || { echo "usage: init-build.sh prepare|build --work DIR" >&2; exit 2; }
[[ ${2:-} == --work && -n ${3:-} ]] || { echo "usage: init-build.sh $cmd --work DIR" >&2; exit 2; }
work=$3
mkdir -p "$work"
work=$(cd "$work" && pwd)
store=$work/store

umask 022
export LC_ALL=C

case $cmd in
  prepare)
    "$repo/scripts/bootstrap.sh" "$lock"
    NL_BOOTSTRAP_UPGRADE=1 "$repo/scripts/bootstrap.sh" "$lock" build
    python3 -I "$repo/scripts/image_lock.py" check
    python3 -I "$repo/scripts/init_toolchain.py" fetch --out "$store"
    rm -rf "$work/rust"
    python3 -I "$repo/scripts/init_toolchain.py" install --store "$store" --prefix "$work/rust"
    ;;
  build)
    version=$(python3 -I -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["toolchain"]["channel"])' "$repo/init/rust-toolchain.toml")
    export PATH=$work/rust/bin:$PATH
    export CARGO_HOME=$work/cargo-home
    # Not rustup's: a rustc that is not exactly the pinned one stops the build.
    [[ $(command -v rustc) == "$work/rust/bin/rustc" && $(rustc -V) == "rustc $version "* ]] \
      || { echo "init-build: rustc is not the pinned $version at $work/rust" >&2; exit 1; }
    export RUSTFLAGS="--remap-path-prefix=$repo=/build --remap-path-prefix=$store/vendor=/vendor --remap-path-prefix=$work/rust=/rust"
    cd "$repo/init"
    cargo build --release --locked --offline \
      --config 'source.crates-io.replace-with="pinned-crates"' \
      --config "source.pinned-crates.directory=\"$store/vendor\""
    mkdir -p "$work/out"
    cp target/release/init "$work/out/init"
    sha256sum "$work/out/init"
    ;;
esac
