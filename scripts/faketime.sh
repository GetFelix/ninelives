# shellcheck shell=bash
# Source this. Runs the rest of the script at another time, with libfaketime
# preloaded (docs/components/image.md, "Reproducibility").
#
#   faketime_on LIB DAYS     LIB from scripts/faketime_lib.py; DAYS ahead, e.g. 400
#
# Only processes that load the library see the offset: static binaries and
# anything run in a chroot that lacks LIB do not. The check at the end covers
# a dynamic process started from here, and stops the script if it sees no offset.

faketime_on() {
  local lib=$1 days=$2 real seen
  [[ -f $lib && $days =~ ^[0-9]+$ ]] || { echo "faketime: bad arguments: $lib $days" >&2; return 1; }
  real=$(date -u +%s)
  export LD_PRELOAD=$lib FAKETIME="+${days}d" FAKETIME_DONT_FAKE_MONOTONIC=1 FAKETIME_NO_CACHE=1
  seen=$(python3 -I -c 'import time; print(int(time.time()))')
  echo "faketime: +${days}d, a preloaded process sees $(( (seen - real) / 86400 )) days ahead"
  (( seen - real >= (days - 1) * 86400 )) || { echo "faketime: the offset is not in effect" >&2; return 1; }
}
