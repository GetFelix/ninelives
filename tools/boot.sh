#!/usr/bin/env bash
# Boot a built qemu x86_64 image on a Linux host, the way CI's boot job does:
# the clean boot, then one byte flipped in the root and one in the hash tree.
#
#   tools/boot.sh --image FILE.raw --out DIR
#
# FILE.raw is the image job's output. DIR gets one serial log per boot. Needs
# docker or podman and network: python3 and gpgv come from the lock's bootstrap
# set (scripts/bootstrap.sh), qemu and the UEFI firmware from its boot set,
# inside the pinned container (images/tools.toml), not from this host. KVM is used if /dev/kvm exists, otherwise qemu emulates and
# a boot takes minutes. Exits non-zero if any boot is not what it should be.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
engine=$(command -v docker || command -v podman) || { echo "needs docker or podman" >&2; exit 1; }
image="" out=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --image) image=$(cd "$(dirname "$2")" && pwd)/$(basename "$2"); shift 2 ;;
    --out) mkdir -p "$2"; out=$(cd "$2" && pwd); shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -f $image && -n $out ]] || { echo "usage: tools/boot.sh --image FILE.raw --out DIR" >&2; exit 2; }
container=$(sed -n 's/^image = "\(.*\)"/\1/p' "$repo/images/tools.toml")
devices=()
[[ -e /dev/kvm ]] && devices=(--device /dev/kvm)
exec "$engine" run --rm "${devices[@]}" \
  -v "$repo":/src:ro -v "$image":/in/image.raw:ro -v "$out":/out \
  "$container" /src/scripts/boot-run.sh all --work /tmp/work --image /in/image.raw --out /out
