#!/usr/bin/env bash
# Resolve the kernel config and build the kernel for one architecture
# (docs/components/kernel.md). Runs on Linux in CI, never on a Mac.
#
#   kernel_build.sh resolve <x86_64|aarch64> <workdir>   verify the tarball, unpack, resolve .config
#   kernel_build.sh build   <x86_64|aarch64> <workdir>   build the kernel image from that .config
#
# The tree lands in <workdir>/src, the build output in <workdir>/out.
set -euo pipefail

cmd=${1:?command}
arch=${2:?arch}
work=$(realpath -m "${3:?workdir}")
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
target=qemu

case $arch in
  x86_64) karch=x86 image=bzImage ;;
  aarch64) karch=arm64 image=Image ;;
  *) echo "unknown architecture $arch" >&2; exit 2 ;;
esac

# One toolchain for both architectures; Clang cross-compiles without a
# separate binutils.
export ARCH=$karch LLVM=1
# S5: nothing about the build machine or time ends up in the image.
export KBUILD_BUILD_USER=ninelives KBUILD_BUILD_HOST=ninelives LOCALVERSION=
# Assigned on its own line so a git failure stops the script: an empty
# timestamp would make the build use the wall clock.
if [[ -z ${KBUILD_BUILD_TIMESTAMP:-} ]]; then
  KBUILD_BUILD_TIMESTAMP=$(git -C "$repo" log -1 --format=@%ct)
fi
if [[ -z $KBUILD_BUILD_TIMESTAMP ]]; then
  echo "no build timestamp: git log printed nothing" >&2
  exit 1
fi
export KBUILD_BUILD_TIMESTAMP

version=$(python3 -I -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["version"])' "$repo/kernel/VERSION")
src=$work/src/linux-$version
out=$work/out

case $cmd in
  resolve)
    # fetch_kernel.py checks the hash, then the signature, before anything is unpacked.
    tarball=$(python3 -I "$repo/scripts/fetch_kernel.py" --out "$work/download")
    rm -rf "$work/src" "$out"
    mkdir -p "$work/src" "$out"
    tar -xJf "$tarball" -C "$work/src" --no-same-owner
    mapfile -t fragments < <(python3 -I "$repo/scripts/check_kernel_config.py" fragments --arch "$arch" --target "$target")
    # -n: start from allnoconfig, so anything no fragment names stays off.
    # The arch hardening.config is what "make hardening.config" would add.
    (cd "$src" && ./scripts/kconfig/merge_config.sh -n -O "$out" \
      kernel/configs/hardening.config "arch/$karch/configs/hardening.config" "${fragments[@]}")
    make -s -C "$src" O="$out" olddefconfig
    ;;
  build)
    make -C "$src" O="$out" -j"$(nproc)" KCFLAGS="-ffile-prefix-map=$src/=" "$image" 2>&1 | tee "$work/build.log"
    # modpost's section mismatches must be exactly the listed ones, offsets
    # dropped, so a new one fails even where the config only warns.
    expected=$repo/kernel/section-mismatches/$arch.txt
    sed -n 's/^WARNING: modpost: vmlinux: section mismatch in reference: //p' "$work/build.log" \
      | sed -E 's/\+0x[0-9a-f]+//' | LC_ALL=C sort -u > "$work/mismatches"
    if [[ -f $expected ]]; then
      grep -v -e '^#' -e '^$' "$expected" | LC_ALL=C sort -u > "$work/mismatches.expected" || true
    else
      : > "$work/mismatches.expected"
    fi
    diff -u "$work/mismatches.expected" "$work/mismatches"
    ;;
  *)
    echo "unknown command $cmd" >&2
    exit 2
    ;;
esac
