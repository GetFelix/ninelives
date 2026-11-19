#!/usr/bin/env bash
# Boot a built image in qemu (docs/components/image.md, "Boot test").
#
# Runs as root inside the pinned bootstrap container (images/tools.toml): CI's
# boot job and tools/boot.sh both start it there. qemu and the UEFI firmware
# are the boot set of images/debian.lock, fetched and verified the way the
# image's tools are, so the machine that boots the image is pinned too.
#
#   boot-run.sh prepare --work DIR
#   boot-run.sh run --work DIR --image FILE.raw --out DIR [--corrupt root|hash]
#   boot-run.sh all --work DIR --image FILE.raw --out DIR
#
# prepare needs the network. run writes serial-<case>.log and boot-<case>.txt
# into --out and exits non-zero
# if the boot did not end as expected. all runs the clean boot and both
# corrupted ones and reports every outcome.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
cmd=${1:-}
[[ -n $cmd ]] && shift
work="" image="" out="" corrupt=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --work) work=$2; shift 2 ;;
    --image) image=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    --corrupt) corrupt=$2; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n $work ]] || { echo "usage: boot-run.sh prepare|run|all --work DIR ..." >&2; exit 2; }
mkdir -p "$work"
work=$(cd "$work" && pwd)
store=$work/store

toml() { # toml FILE KEY...: one value from a TOML file
  python3 -I -c 'import sys,tomllib; d=tomllib.load(open(sys.argv[1],"rb"))
for k in sys.argv[2:]: d=d[k]
print(d)' "$@"
}

prepare() {
  if ! command -v python3 >/dev/null; then
    # python3, gpgv, xz-utils and ca-certificates, each .deb checked against
    # debian.lock before dpkg unpacks it.
    "$repo/scripts/bootstrap.sh" "$repo/images/debian.lock"
  fi
  python3 -I "$repo/scripts/image_lock.py" check
  # The lock must be what resolution gives for the pins, so a hand edit cannot add a package.
  python3 -I "$repo/scripts/image_lock.py" generate "$work/debian.lock.regenerated"
  diff -u "$repo/images/debian.lock" "$work/debian.lock.regenerated"
  python3 -I "$repo/scripts/fetch_image.py" --out "$store" --boot

  # qemu and the firmware, from the verified store, at exactly the locked versions.
  local snapshot release keyring=/usr/share/keyrings/ninelives-debian.gpg mirror=$store/mirror
  snapshot=$(toml "$repo/images/tools.toml" debian snapshot)
  release=$(toml "$repo/images/tools.toml" boot release)
  python3 -I "$repo/scripts/image_lock.py" keyring "$keyring"
  rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list
  cat > /etc/apt/apt.conf.d/99ninelives <<'CONF'
Acquire::By-Hash "no";
Acquire::Languages "none";
Acquire::CompressionTypes::Order { "xz"; };
Acquire::Retries "0";
Acquire::Check-Valid-Until "false";
CONF
  cat > /etc/apt/sources.list.d/ninelives.sources <<SRC
Types: deb
URIs: file://$mirror/archive/debian/$snapshot
Suites: $release $release-updates
Components: main
Architectures: amd64
Signed-By: $keyring

Types: deb
URIs: file://$mirror/archive/debian-security/$snapshot
Suites: $release-security
Components: main
Architectures: amd64
Signed-By: $keyring
SRC
  apt-get update
  local pkgs
  mapfile -t pkgs < <(python3 -I "$repo/scripts/image_lock.py" boot)
  # --reinstall: the bootstrap may have put some of these on the machine, and
  # the same version would otherwise stay as it is.
  apt-get install -y --no-install-recommends --allow-downgrades --reinstall "${pkgs[@]}"
  local installed
  installed=$(dpkg-query -W -f '${db:Status-Abbrev}${Package}=${Version}\n' | sed -n 's/^ii //p' | LC_ALL=C sort)
  comm -23 <(printf '%s\n' "${pkgs[@]}" | LC_ALL=C sort) <(echo "$installed") | grep . \
    && { echo "the packages above are not installed at the locked version" >&2; exit 1; }
  qemu-system-x86_64 --version | head -n1
  test -s /usr/share/OVMF/OVMF_CODE_4M.fd
  test -s /usr/share/OVMF/OVMF_VARS_4M.fd
  return 0
}

run_case() { # run_case NAME [root|hash]
  local name=$1 args=()
  [[ -n ${2:-} ]] && args=(--corrupt "$2")
  mkdir -p "$out"
  trap 'chmod -R a+rX "$out"' RETURN  # the container runs as root; the host reads the logs
  python3 -I "$repo/scripts/boot_image.py" --image "$image" \
    --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
    --log "$out/serial-$name.log" "${args[@]}" 2>&1 | tee "$out/boot-$name.txt"
}

case $cmd in
  prepare) prepare ;;
  run)
    [[ -f $image && -n $out ]] || { echo "run needs --image and --out" >&2; exit 2; }
    run_case "${corrupt:-clean}" "$corrupt"
    ;;
  all)
    [[ -f $image && -n $out ]] || { echo "all needs --image and --out" >&2; exit 2; }
    prepare
    status=0
    run_case clean || status=1
    run_case corrupt-root root || status=1
    run_case corrupt-hash hash || status=1
    exit $status
    ;;
  *) echo "usage: boot-run.sh prepare|run|all --work DIR ..." >&2; exit 2 ;;
esac
