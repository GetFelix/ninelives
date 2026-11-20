#!/usr/bin/env bash
# Build the qemu production image on a Linux host, the way CI does.
# Nothing built here is signed or published (docs/components/image.md).
#
#   NL_ARCH=x86_64|aarch64 tools/image.sh --kernel DIR --init FILE --out DIR [--work DIR]
#
# The build is native: NL_ARCH (x86_64 if unset) must be the host's own
# architecture, and the kernel and init must be of it too. DIR for --kernel
# holds the bzImage (aarch64: Image) and .config of the kernel job's
# kernel-qemu-ARCH artifact; FILE is the init binary
# (cargo build --release --locked in init/). Needs docker or podman, and
# network for the fetch phase. The container has no git: the commit and its
# time are read here and passed in (COMMIT and SOURCE_DATE_EPOCH in the
# environment win, for CI).
#
# Two containers, so nothing that fetched or installed anything runs with the
# relaxed settings. The fetch phase runs as root in a container with the
# engine's default seccomp profile and /proc masking, and is committed. The
# build phase runs from that result as an unprivileged user, never root, with
# seccomp and AppArmor off and /proc unmasked: a user namespace with a fresh
# /proc needs them (docs/components/image.md, "Build environment"). Every path
# is mounted at its own host path, so a build from another checkout or work
# directory sees another path, as the rebuild wants. On Ubuntu 24.04 the host
# also needs kernel.apparmor_restrict_unprivileged_userns=0.
#
# Passed through when set: NL_FAKETIME_DAYS, NL_BUILD_UID, TZ, USER, LOGNAME,
# HOME. NL_CPUS=0 pins the container to that CPU list (docker --cpuset-cpus).
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
engine=$(command -v docker || command -v podman) || { echo "needs docker or podman" >&2; exit 1; }
kernel="" init="" out="" work=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --kernel) kernel=$(cd "$2" && pwd); shift 2 ;;
    --init) init=$(cd "$(dirname "$2")" && pwd)/$(basename "$2"); shift 2 ;;
    --out) mkdir -p "$2"; out=$(cd "$2" && pwd); shift 2 ;;
    --work) mkdir -p "$2"; work=$(cd "$2" && pwd); shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n $kernel && -n $init && -n $out ]] || { echo "usage: tools/image.sh --kernel DIR --init FILE --out DIR [--work DIR]" >&2; exit 2; }
if [[ -z $work ]]; then
  work=$(mktemp -d "${TMPDIR:-/tmp}/ninelives-work.XXXXXX")
fi
image=$(sed -n 's/^image = "\(.*\)"/\1/p' "$repo/images/tools.toml")
commit=${COMMIT:-$(git -C "$repo" rev-parse HEAD)}
epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo" log -1 --format=%ct)}
# podman spells the /proc unmasking differently from docker.
case $engine in
  *podman) proc_opt=unmask=ALL ;;
  *) proc_opt=systempaths=unconfined ;;
esac
env_args=(-e COMMIT="$commit" -e SOURCE_DATE_EPOCH="$epoch" -e NL_ARCH="${NL_ARCH:-x86_64}")
for v in NL_FAKETIME_DAYS NL_BUILD_UID TZ USER LOGNAME HOME; do
  [[ -z ${!v:-} ]] || env_args+=(-e "$v=${!v}")
done
cpu_args=()
[[ -z ${NL_CPUS:-} ]] || cpu_args=(--cpuset-cpus="$NL_CPUS")
uid=${NL_BUILD_UID:-1000}
build_home=${HOME:-}
if [[ -z $build_home || $build_home == / || $build_home == /root ]]; then build_home=$work/home; fi
mounts=(-v "$repo:$repo:ro" -v "$kernel:$kernel:ro" -v "$(dirname "$init"):$(dirname "$init"):ro" -v "$work:$work")
args=("$repo/scripts/image-build.sh" --kernel "$kernel" --init "$init" --out "$work/out" --work "$work")
tag=ninelives-build-$$
cid=""
# shellcheck disable=SC2329  # run by the EXIT trap
cleanup() {
  [[ -z $cid ]] || "$engine" rm -f "$cid" >/dev/null 2>&1 || true
  "$engine" rmi -f "$tag" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Phase one: fetch, verify, install the tools, prepare the user.
cid=$("$engine" create "${cpu_args[@]}" "${env_args[@]}" "${mounts[@]}" "$image" "${args[@]}")
"$engine" start -a "$cid"
"$engine" commit "$cid" "$tag" >/dev/null
"$engine" rm -f "$cid" >/dev/null
cid=""

# Phase two: the build, as the user, in its own user and network namespaces.
rc=0
"$engine" run --rm "${cpu_args[@]}" --user "$uid:$uid" \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt "$proc_opt" \
  "${env_args[@]}" -e NL_IMAGE_PHASE=build -e HOME="$build_home" "${mounts[@]}" \
  "$tag" unshare --user --map-current-user --net -- "${args[@]}" || rc=$?
# A failed run still hands over what it wrote (a rejected check's report).
mkdir -p "$out"
cp -R "$work/out/." "$out/"
exit "$rc"
