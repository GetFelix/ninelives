#!/usr/bin/env bash
# Install the bootstrap packages into the pinned base container, each checked
# against images/debian.lock (docs/components/image.md, "Bootstrap").
#
#   bootstrap.sh [LOCK [SET]]
#
# Runs as root before python3 exists, so it uses only what the digest-pinned
# Debian image has: bash, awk, sha256sum, dpkg and apt's download helper. No
# apt index is read and nothing is resolved: the lock names every .deb, the
# bytes are checked against its SHA-256, and dpkg unpacks them. A package the
# container already has is left as it is, since the digest vouches for it.
#
# SET is the lock set to install (default bootstrap). The init build also
# installs `build`, with NL_BOOTSTRAP_UPGRADE=1: there a package is installed
# unless the container already has the lock's exact version, because libc6-dev
# needs the libc6 of its own build and the digest's may be older.
#
# The seams below exist for scripts/tests/test_bootstrap.py.
#   NL_BOOTSTRAP_GET         URL DEST, fetches one file (default: apt-helper)
#   NL_BOOTSTRAP_DPKG        installs the .debs given as arguments (default: dpkg -i)
#   NL_BOOTSTRAP_DPKG_QUERY  dpkg-query
#   NL_BOOTSTRAP_DIR         where the .debs are kept
#   NL_BOOTSTRAP_UPGRADE     1 to replace an installed package of another version
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
lock=${1:-$repo/images/debian.lock}
set_name=${2:-bootstrap}
[[ $set_name =~ ^[a-z]+$ ]] || { echo "bootstrap: bad set name $set_name" >&2; exit 1; }
dir=${NL_BOOTSTRAP_DIR:-/var/cache/ninelives-bootstrap}
query=${NL_BOOTSTRAP_DPKG_QUERY:-dpkg-query}
base_url=${NL_BOOTSTRAP_URL:-http://snapshot.debian.org/archive}

get() { # get URL DEST
  if [[ -n ${NL_BOOTSTRAP_GET:-} ]]; then
    "$NL_BOOTSTRAP_GET" "$1" "$2"
  else
    # The URL is plain http: the SHA-256 from the lock is the check, and the
    # image has no CA certificates yet. apt-helper downloads as root here.
    /usr/lib/apt/apt-helper -o APT::Sandbox::User=root -o Acquire::Retries=5 download-file "$1" "$2"
  fi
}

install_debs() {
  if [[ -n ${NL_BOOTSTRAP_DPKG:-} ]]; then
    "$NL_BOOTSTRAP_DPKG" "$@"
  else
    DEBIAN_FRONTEND=noninteractive dpkg -i "$@"
  fi
}

installed_version() { # installed_version NAME: the version, or nothing
  # shellcheck disable=SC2016  # dpkg-query format, not shell
  "$query" -W -f '${db:Status-Abbrev} ${Version}\n' "$1" 2>/dev/null | sed -n 's/^ii  *//p'
}

snapshot=$(sed -n 's/^snapshot = "\(.*\)"$/\1/p' "$lock" | head -n1)
[[ $snapshot =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "bootstrap: no snapshot in $lock" >&2; exit 1; }

# One line per package: name version sha256 archive filename. The lock is
# written by scripts/image_lock.py, one key per line.
mapfile -t entries < <(awk -v want="[[$set_name.package]]" '
  /^\[\[/ { if (inset) emit(); inset = ($0 == want); next }
  /^\[/ { if (inset) emit(); inset = 0; next }
  inset && match($0, /^[a-z0-9_]+ = "/) {
    key = substr($0, 1, index($0, " ") - 1)
    val = substr($0, RLENGTH + 1); sub(/"$/, "", val)
    f[key] = val
  }
  function emit() { if (f["name"] != "") print f["name"], f["version"], f["sha256"], f["archive"], f["filename"]; delete f }
  END { if (inset) emit() }
' "$lock")
[[ ${#entries[@]} -gt 0 ]] || { echo "bootstrap: $lock has no [[$set_name.package]]" >&2; exit 1; }

mkdir -p "$dir"
debs=() want=()
for line in "${entries[@]}"; do
  read -r name version sha archive filename <<<"$line"
  [[ $sha =~ ^[0-9a-f]{64}$ && $filename =~ ^pool/[A-Za-z0-9+._~%/-]+\.deb$ && $archive =~ ^debian(-security)?$ ]] \
    || { echo "bootstrap: bad lock entry for $name" >&2; exit 1; }
  have=$(installed_version "$name" || true)
  if [[ -n $have && ( ${NL_BOOTSTRAP_UPGRADE:-} != 1 || $have == "$version" ) ]]; then
    continue
  fi
  deb=$dir/$sha.deb
  [[ -s $deb ]] || get "$base_url/$archive/$snapshot/$filename" "$deb"
  got=$(sha256sum "$deb" | cut -d' ' -f1)
  if [[ $got != "$sha" ]]; then
    rm -f "$deb"
    echo "bootstrap: $name $version is $got, the lock pins $sha" >&2
    exit 1
  fi
  debs+=("$deb")
  want+=("$name=$version")
done

# A Pre-Depends is checked when the dependent is unpacked, so python3 cannot go
# in beside python3-minimal in one call. dpkg carries on past it and configures
# the rest, so another call with what is left finds it satisfied. Calls repeat
# while the left-over set shrinks; a call that gets nowhere is an error.
prev=-1
while :; do
  todo=()
  for i in "${!want[@]}"; do
    [[ $(installed_version "${want[$i]%%=*}") == "${want[$i]#*=}" ]] || todo+=("${debs[$i]}")
  done
  [[ ${#todo[@]} -gt 0 ]] || break
  if [[ $prev -ge 0 && ${#todo[@]} -ge $prev ]]; then
    echo "bootstrap: dpkg made no progress, ${#todo[@]} packages are still not installed" >&2
    exit 1
  fi
  prev=${#todo[@]}
  install_debs "${todo[@]}" || echo "bootstrap: dpkg did not finish, retrying what is left" >&2
done
for w in "${want[@]}"; do
  [[ $(installed_version "${w%%=*}") == "${w#*=}" ]] || { echo "bootstrap: $w is not installed" >&2; exit 1; }
done
echo "bootstrap: ${#debs[@]} $set_name packages installed, each checked against the lock"
