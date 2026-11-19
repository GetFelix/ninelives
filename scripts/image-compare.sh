#!/usr/bin/env bash
# Compare two builds of the image (docs/components/image.md, "Reproducibility").
#
# Runs as root inside the pinned bootstrap container (images/tools.toml), in
# CI's image-compare job. diffoscope is the compare set of images/debian.lock,
# fetched and verified the way the image's tools are, so a report comes from
# pinned bytes, not from the runner or PyPI.
#
#   image-compare.sh --a DIR --b DIR --report DIR --work DIR
#
# Needs the network for the fetch phase only. Exits non-zero, with the report
# written, if the builds differ. It does not retry.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1 LC_ALL=C
a="" b="" report="" work=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --a) a=$2; shift 2 ;;
    --b) b=$2; shift 2 ;;
    --report) report=$2; shift 2 ;;
    --work) work=$2; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n $a && -n $b && -n $report && -n $work ]] || { echo "usage: image-compare.sh --a DIR --b DIR --report DIR --work DIR" >&2; exit 2; }
mkdir -p "$work" "$report"
work=$(cd "$work" && pwd)
store=$work/store

toml() { # toml FILE KEY...: one value from a TOML file
  python3 -I -c 'import sys,tomllib; d=tomllib.load(open(sys.argv[1],"rb"))
for k in sys.argv[2:]: d=d[k]
print(d)' "$@"
}

if ! command -v python3 >/dev/null; then
  "$repo/scripts/bootstrap.sh" "$repo/images/debian.lock"
fi
python3 -I "$repo/scripts/image_lock.py" check
# The lock must be what resolution gives for the pins, so a hand edit cannot add a package.
python3 -I "$repo/scripts/image_lock.py" generate "$work/debian.lock.regenerated"
diff -u "$repo/images/debian.lock" "$work/debian.lock.regenerated"
python3 -I "$repo/scripts/fetch_image.py" --out "$store" --compare

snapshot=$(toml "$repo/images/tools.toml" debian snapshot)
release=$(toml "$repo/images/tools.toml" compare release)
keyring=/usr/share/keyrings/ninelives-debian.gpg
mirror=$store/mirror
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
mapfile -t pkgs < <(python3 -I "$repo/scripts/image_lock.py" compare)
# --reinstall: the bootstrap may have put some of these on the machine.
apt-get install -y --no-install-recommends --allow-downgrades --reinstall "${pkgs[@]}"
installed=$(dpkg-query -W -f '${db:Status-Abbrev}${Package}=${Version}\n' | sed -n 's/^ii //p' | sort)
if comm -23 <(printf '%s\n' "${pkgs[@]}" | sort) <(echo "$installed") | grep .; then
  echo "the packages above are not installed at the locked version" >&2
  exit 1
fi
diffoscope --version

# diffoscope's scratch space stays under the work directory.
export TMPDIR=$work/tmp
mkdir -p "$TMPDIR"
python3 -I "$repo/scripts/image_compare.py" --a "$a" --b "$b" --report "$report"
