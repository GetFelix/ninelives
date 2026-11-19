#!/usr/bin/env bash
# Build the qemu x86_64 production image on a Linux host, the way CI does.
# Nothing built here is signed or published (docs/components/image.md).
#
#   tools/image.sh --kernel DIR --init FILE --out DIR
#
# DIR for --kernel holds the bzImage and .config of the kernel job's
# kernel-qemu-x86_64 artifact; FILE is the init binary
# (cargo build --release --locked in init/). Needs docker or podman, and
# network for the fetch phase. The container has no git: the commit and its
# time are read here and passed in. The container is privileged because the build
# mounts the root filesystem it makes to check it.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
engine=$(command -v docker || command -v podman) || { echo "needs docker or podman" >&2; exit 1; }
kernel="" init="" out=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --kernel) kernel=$(cd "$2" && pwd); shift 2 ;;
    --init) init=$(cd "$(dirname "$2")" && pwd)/$(basename "$2"); shift 2 ;;
    --out) mkdir -p "$2"; out=$(cd "$2" && pwd); shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n $kernel && -n $init && -n $out ]] || { echo "usage: tools/image.sh --kernel DIR --init FILE --out DIR" >&2; exit 2; }
image=$(sed -n 's/^image = "\(.*\)"/\1/p' "$repo/images/tools.toml")
commit=$(git -C "$repo" rev-parse HEAD)
epoch=$(git -C "$repo" log -1 --format=%ct)
exec "$engine" run --rm --privileged \
  -e COMMIT="$commit" -e SOURCE_DATE_EPOCH="$epoch" \
  -v "$repo":/src:ro -v "$kernel":/in/kernel:ro -v "$init":/in/init:ro -v "$out":/out \
  "$image" /src/scripts/image-build.sh --kernel /in/kernel --init /in/init --out /out --work /tmp/work
