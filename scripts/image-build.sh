#!/usr/bin/env bash
# Build the qemu production image for NL_ARCH, x86_64 or aarch64 (docs/components/image.md).
#
# Runs as root inside the pinned bootstrap container (images/tools.toml);
# tools/image.sh starts it there, and CI runs it in the same container. The
# fetch phase's own tools are installed first by scripts/bootstrap.sh, each
# .deb checked against images/debian.lock.
# Two phases: fetch downloads and verifies every input and has the network,
# then the build re-runs itself in a network namespace with only loopback and
# reads nothing but the verified store.
#
#   image-build.sh --kernel DIR --init FILE --out DIR [--work DIR]
#
# DIR for --kernel is the kernel-qemu-ARCH artifact (bzImage or Image, .config).
# NL_ARCH picks the architecture (x86_64 if unset); the machine running this must be of it.
# COMMIT and SOURCE_DATE_EPOCH come from the environment (the container has no git).
# NL_FAKETIME_DAYS=N runs the build phase N days ahead of the real clock.
set -euo pipefail

# The caller's umask must not reach the build: apt reads the keyring and the store
# as its own user, and a mode left by umask 077 locks it out.
umask 022
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
# shellcheck source=scripts/arch.sh
. "$repo/scripts/arch.sh"
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
bootstrap_base=/var/lib/ninelives-bootstrap-base
meta=$work/meta

installed_list() { # name=version of every installed package, sorted the same way everywhere
  dpkg-query -W -f '${db:Status-Abbrev}${Package}=${Version}\n' | sed -n 's/^ii //p' | LC_ALL=C sort
}

toml() { # toml FILE KEY...: one value from a TOML file
  python3 -I -c 'import sys,tomllib; d=tomllib.load(open(sys.argv[1],"rb"))
for k in sys.argv[2:]: d=d[k]
print(d)' "$@"
}

if [[ ${NL_IMAGE_PHASE:-fetch} == fetch ]]; then
  # The container has no git, so these come from the caller. Both end up in the image.
  [[ ${COMMIT:-} =~ ^[0-9a-f]{40}$ ]] || { echo "COMMIT must be a 40-digit lowercase hex commit id (the container has no git)" >&2; exit 2; }
  [[ ${SOURCE_DATE_EPOCH:-} =~ ^[0-9]+$ ]] || { echo "SOURCE_DATE_EPOCH must be a number of seconds (the container has no git)" >&2; exit 2; }
  if ! command -v python3 >/dev/null; then
    # What the container holds before the bootstrap touches it, so the build
    # phase can tell what the bootstrap added or changed.
    installed_list > "$bootstrap_base"
    # python3, gpgv, xz-utils and ca-certificates, each .deb checked against
    # debian.lock before dpkg unpacks it. No apt resolution, no keyring.
    "$repo/scripts/bootstrap.sh" "$lock"
  fi

  python3 -I "$repo/scripts/image_lock.py" check
  # The lock must be what resolution gives for the pins, so a hand edit cannot add a package.
  python3 -I "$repo/scripts/image_lock.py" generate "$work/debian.lock.regenerated"
  diff -u "$lock" "$work/debian.lock.regenerated"
  python3 -I "$repo/scripts/fetch_image.py" --out "$store"

  export NL_IMAGE_PHASE=build
  exec unshare --net -- "$0" "${args[@]}"
fi

# ---- build phase: no network from here on --------------------------------
# A build step that reaches the network means the namespace is not doing its job (I2).
if python3 -I -c 'import socket,sys; socket.create_connection(("1.1.1.1", 53), timeout=3)' 2>/dev/null; then
  echo "the network is reachable in the build phase" >&2
  exit 1
fi
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
Architectures: $deb_arch
Signed-By: $keyring

Types: deb
URIs: file://$mirror/archive/debian-security/$snapshot
Suites: $1-security
Components: main
Architectures: $deb_arch
Signed-By: $keyring
SRC
}

# The bootstrap step installs only what the lock names, but it runs before the
# lock is verified against the signed archive. Fail if it upgraded a package
# the container had, purge what it added beyond the locked tools, then install
# the tools from the verified store.
[[ -s $bootstrap_base ]] || { echo "the bootstrap step did not record the container's packages" >&2; exit 1; }
mapfile -t tool_pkgs < <(python3 -I "$repo/scripts/image_lock.py" tools)
tool_names=()
for p in "${tool_pkgs[@]}"; do tool_names+=("${p%%=*}"); done
export LC_ALL=C
installed_list > "$work/installed-after-bootstrap"
upgraded=$(awk -F= 'NR==FNR {v[$1]=$2; next} ($1 in v) && v[$1] != $2 {print $1}' "$bootstrap_base" "$work/installed-after-bootstrap")
if [[ -n $upgraded ]]; then
  echo "the bootstrap step upgraded packages that were in the container: $upgraded" >&2
  exit 1
fi
added=$(comm -13 <(cut -d= -f1 "$bootstrap_base" | sort) <(cut -d= -f1 "$work/installed-after-bootstrap" | sort) \
  | comm -23 - <(printf '%s\n' "${tool_names[@]}" | sort))
if [[ -n $added ]]; then
  echo "purging what the bootstrap step added and the tools lock does not name: $added"
  # shellcheck disable=SC2086  # one word per package
  apt-get purge -y $added
fi

# The build tools, from the verified store, at exactly the locked versions.
rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list
write_apt_config /etc/apt
sources "$(toml "$repo/images/tools.toml" tools release)" > /etc/apt/sources.list.d/ninelives.sources
apt-get update
# --reinstall, because the bootstrap already put some of these on the machine
# and a plain install of the same version would leave them as they are. The
# verified store, not the bootstrap download, is what the image build uses.
apt-get install -y --no-install-recommends --allow-downgrades --reinstall "${tool_pkgs[@]}"
diff <(printf '%s\n' "${tool_pkgs[@]}" | sort) \
  <(installed_list | awk -F= 'NR==FNR {t[$1]=1; next} $1 in t' <(printf '%s\n' "${tool_names[@]}") -) \
  || { echo "an installed tool is not the locked version" >&2; exit 1; }
# Nothing else is on the machine: the container's own packages, unchanged, and the locked tools.
expected=$( { awk -F= 'NR==FNR {t[$1]=1; next} !($1 in t)' <(printf '%s\n' "${tool_names[@]}") "$bootstrap_base"; printf '%s\n' "${tool_pkgs[@]}"; } | sort)
installed=$(installed_list)
if [[ $expected != "$installed" ]]; then
  echo "the installed packages are not the container's plus the locked tools:" >&2
  diff <(echo "$expected") <(echo "$installed") >&2 || true
  exit 1
fi

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
# The rebuild runs the rest of the build at another time. The fetch phase has
# already happened: TLS certificates and signatures are checked at real time.
if [[ -n ${NL_FAKETIME_DAYS:-} ]]; then
  # shellcheck source=/dev/null
  . "$repo/scripts/faketime.sh"
  faketime_on "$(python3 -I "$repo/scripts/faketime_lib.py" --store "$store" --out "$work/faketime")" "$NL_FAKETIME_DAYS"
fi
PYTHONPATH=$mkosi_dir python3 -B -m mkosi \
  --directory "$repo/images/qemu-$NL_ARCH" --force \
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

name=ninelives-dev-${COMMIT:0:12}-production-$NL_ARCH
kernel_version=$(toml "$repo/kernel/VERSION" version)
python3 -I "$repo/scripts/image_assemble.py" --root "$root" --kernel "$kernel_dir/$(toml "$repo/images/qemu-$NL_ARCH/target.toml" kernel_file)" --kernel-version "$kernel_version" \
  --commit "$COMMIT" --source-date-epoch "$SOURCE_DATE_EPOCH" --out "$out" --name "$name"

python3 -I "$repo/scripts/image_inventory.py" --meta "$meta" --store "$store" --kernel-dir "$kernel_dir" \
  --commit "$COMMIT" --source-date-epoch "$SOURCE_DATE_EPOCH" --init "$init_bin" --output "$out/$name.inventory.json"
cp "$meta/origins.json" "$out/$name.origins.json"
if ! python3 -I "$repo/scripts/check_image.py" --image "$out/$name.raw" --meta "$meta" --commit "$COMMIT" --kernel "$kernel_dir" --source-date-epoch "$SOURCE_DATE_EPOCH"; then
  # A starting point for images/executables.toml, to review by hand.
  python3 -I "$repo/scripts/check_image.py" --image "$out/$name.raw" --meta "$meta" --commit "$COMMIT" --kernel "$kernel_dir" --source-date-epoch "$SOURCE_DATE_EPOCH" \
    --propose-executables > "$out/executables.proposed.toml" || true
  exit 1
fi

(cd "$out" && sha256sum -- "$name".* > SHA256SUMS)
echo "image: $out/$name.raw"
