# Sourced by the image scripts: the architecture of the build.
#
# NL_ARCH is x86_64 (the default) or aarch64. Builds are native, so it is the
# architecture of the machine running the script and of the image it builds.
# Sets deb_arch (Debian's name for it) and lock (the architecture's
# debian.lock); needs $repo.
# shellcheck shell=bash disable=SC2034,SC2154
NL_ARCH=${NL_ARCH:-x86_64}
export NL_ARCH
case $NL_ARCH in
  x86_64) deb_arch=amd64 lock=$repo/images/debian.lock ;;
  aarch64) deb_arch=arm64 lock=$repo/images/debian-aarch64.lock ;;
  *) echo "NL_ARCH=$NL_ARCH: expected x86_64 or aarch64" >&2; exit 2 ;;
esac
