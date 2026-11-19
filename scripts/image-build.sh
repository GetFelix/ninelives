#!/usr/bin/env bash
# Build the x86_64 qemu production image (docs/components/image.md).
#
# Runs as root inside the pinned bootstrap container (images/tools.toml);
# tools/image.sh starts it there, and CI runs it in the same container.
# Two phases: fetch downloads and verifies every input and has the network,
# then the build re-runs itself in a network namespace with only loopback and
# reads nothing but the verified store.
#
#   image-build.sh --kernel DIR --init FILE --out DIR [--work DIR]
#
# DIR for --kernel is the kernel-qemu-x86_64 artifact (bzImage, .config).
# COMMIT and SOURCE_DATE_EPOCH come from the environment, or from git.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
args=("$@")
kernel_dir="" init_bin="" out="" work=/work
while [[ $# -gt 0 ]]; do
  case $1 in
    --kernel) kernel_dir=$2; shift 2 ;;
    --init) init_bin=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    --work) work=$2; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n $kernel_dir && -n $init_bin && -n $out ]] || { echo "usage: image-build.sh --kernel DIR --init FILE --out DIR" >&2; exit 2; }
kernel_dir=$(cd "$kernel_dir" && pwd)
init_bin=$(cd "$(dirname "$init_bin")" && pwd)/$(basename "$init_bin")
mkdir -p "$out" "$work"
out=$(cd "$out" && pwd)
work=$(cd "$work" && pwd)
store=$work/store
meta=$work/meta

toml() { # toml FILE KEY...: one value from a TOML file
  python3 -I -c 'import sys,tomllib; d=tomllib.load(open(sys.argv[1],"rb"))
for k in sys.argv[2:]: d=d[k]
print(d)' "$@"
}

snapshot_sources() { # snapshot_sources SNAPSHOT: apt reads the snapshot over plain http
  # apt checks the Release files against the container's own archive keyring.
  # This is the one unpinned step: it installs the python3 and gpgv that the
  # fetch phase runs on. The snapshot fixes what it gets.
  local keyring
  keyring=$(sed -n 's/^Signed-By: //p' /etc/apt/sources.list.d/debian.sources | head -n1)
  test -s "$keyring"
  rm -f /etc/apt/sources.list.d/debian.sources
  cat > /etc/apt/sources.list.d/snapshot.sources <<SRC
Types: deb
URIs: http://snapshot.debian.org/archive/debian/$1
Suites: trixie trixie-updates
Components: main
Signed-By: $keyring

Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/$1
Suites: trixie-security
Components: main
Signed-By: $keyring
SRC
}

if [[ ${NL_IMAGE_PHASE:-fetch} == fetch ]]; then
  if ! command -v python3 >/dev/null; then
    # Before python3 there is no way to read tools.toml, so sed does it.
    snap=$(sed -n 's/^snapshot = "\(.*\)"/\1/p' "$repo/images/tools.toml")
    snapshot_sources "$snap"
    apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Retries=5 update
    apt-get -o Acquire::Retries=5 install -y --no-install-recommends ca-certificates git gpgv python3 xz-utils
  fi
  git config --global --add safe.directory "$repo"
  export COMMIT=${COMMIT:-$(git -C "$repo" rev-parse HEAD)}
  export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$repo" log -1 --format=%ct)}

  python3 -I "$repo/scripts/image_lock.py" check
  # The lock must be what resolution gives for the pins, so a hand edit cannot add a package.
  python3 -I "$repo/scripts/image_lock.py" generate "$work/debian.lock.regenerated"
  diff -u "$repo/images/debian.lock" "$work/debian.lock.regenerated"
  python3 -I "$repo/scripts/fetch_image.py" --out "$store"

  export NL_IMAGE_PHASE=build
  exec unshare --net -- "$0" "${args[@]}"
fi

# ---- build phase: no network from here on --------------------------------
snapshot=$(toml "$repo/images/tools.toml" debian snapshot)
release_root=$(toml "$repo/images/tools.toml" root release)
mirror=$store/mirror
keyring=/usr/share/keyrings/ninelives-debian.gpg

python3 -I "$repo/scripts/image_lock.py" keyring "$keyring"
write_apt_config() { # write_apt_config ETC_APT_DIR
  mkdir -p "$1/sources.list.d" "$1/apt.conf.d"
  cat > "$1/apt.conf.d/99ninelives" <<'CONF'
Acquire::By-Hash "no";
Acquire::Languages "none";
Acquire::CompressionTypes::Order { "xz"; };
Acquire::Retries "0";
Acquire::Check-Valid-Until "false";
CONF
}
sources() { # sources RELEASE
  cat <<SRC
Types: deb
URIs: file://$mirror/archive/debian/$snapshot
Suites: $1 $1-updates
Components: main
Architectures: amd64
Signed-By: $keyring

Types: deb
URIs: file://$mirror/archive/debian-security/$snapshot
Suites: $1-security
Components: main
Architectures: amd64
Signed-By: $keyring
SRC
}

# The build tools, from the verified store, at exactly the locked versions.
rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list
write_apt_config /etc/apt
sources "$(toml "$repo/images/tools.toml" tools release)" > /etc/apt/sources.list.d/ninelives.sources
apt-get update
mapfile -t tool_pkgs < <(python3 -I "$repo/scripts/image_lock.py" tools)
apt-get install -y --no-install-recommends --allow-downgrades "${tool_pkgs[@]}"

# mkosi, from the verified archive.
mkosi_ver=$(toml "$repo/images/tools.toml" mkosi version)
mkdir -p "$work/mkosi"
tar -xzf "$store/mkosi/mkosi-$mkosi_ver.tar.gz" -C "$work/mkosi"
mkosi_dir=$work/mkosi/mkosi-$mkosi_ver

sandbox=$work/sandbox
rm -rf "$sandbox" "$work/mkosi-out"
write_apt_config "$sandbox/etc/apt"
sources "$release_root" > "$sandbox/etc/apt/sources.list.d/mkosi.sources"

mapfile -t root_pkgs < <(python3 -I -c '
import sys, tomllib
for p in tomllib.load(open(sys.argv[1], "rb"))["package"]: print(p["name"])' "$repo/images/packages.toml")
pkg_args=()
for p in "${root_pkgs[@]}"; do pkg_args+=(--package "$p"); done

export SOURCE_DATE_EPOCH
PYTHONPATH=$mkosi_dir python3 -B -m mkosi \
  --directory "$repo/images/qemu-x86_64" --force \
  --release "$release_root" \
  --output-directory "$work/mkosi-out" \
  --workspace-directory "$work/mkosi-ws" \
  --cache-directory "$work/mkosi-cache" \
  --package-cache-directory "$work/mkosi-pkgcache" \
  --local-mirror "file://$mirror" \
  --sandbox-tree "$sandbox" \
  "${pkg_args[@]}" \
  build
root=$work/mkosi-out/ninelives

python3 -I "$repo/scripts/image_stage.py" --root "$root" --store "$store" --init "$init_bin" \
  --commit "$COMMIT" --source-date-epoch "$SOURCE_DATE_EPOCH" --meta "$meta"

name=ninelives-dev-${COMMIT:0:12}-production-x86_64
kernel_version=$(toml "$repo/kernel/VERSION" version)
python3 -I "$repo/scripts/image_assemble.py" --root "$root" --kernel "$kernel_dir/bzImage" --kernel-version "$kernel_version" \
  --commit "$COMMIT" --source-date-epoch "$SOURCE_DATE_EPOCH" --out "$out" --name "$name"

python3 -I "$repo/scripts/image_inventory.py" --meta "$meta" --store "$store" --kernel-dir "$kernel_dir" \
  --commit "$COMMIT" --source-date-epoch "$SOURCE_DATE_EPOCH" --output "$out/$name.inventory.json"
cp "$meta/origins.json" "$out/$name.origins.json"
if ! python3 -I "$repo/scripts/check_image.py" --image "$out/$name.raw" --meta "$meta" --commit "$COMMIT"; then
  # A starting point for images/executables.toml, to review by hand.
  python3 -I "$repo/scripts/check_image.py" --image "$out/$name.raw" --meta "$meta" --commit "$COMMIT" \
    --propose-executables > "$out/executables.proposed.toml" || true
  exit 1
fi

(cd "$out" && sha256sum -- "$name".* > SHA256SUMS)
echo "image: $out/$name.raw"
