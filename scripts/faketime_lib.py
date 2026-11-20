#!/usr/bin/env python3
"""Unpack libfaketime from the verified store and print the path of its library.

  faketime_lib.py --store DIR --out DIR

DIR for --store is what fetch_image.py wrote. The .deb is checked against the
SHA-256 in the lock of NL_ARCH again before dpkg-deb unpacks it. Nothing is
installed: the rebuilds preload the library (scripts/faketime.sh).
"""
import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_image  # noqa: E402
import image_common as ic  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    lock = ic.load_toml(ic.lock_path())
    debs = [p for p in lock["faketime"]["package"] if p["name"] == "libfaketime"]
    if len(debs) != 1:
        raise SystemExit("the lock's faketime set does not hold exactly one libfaketime")
    p = debs[0]
    deb = fetch_image.mirror_path(args.store, lock["snapshot"], p["archive"], p["filename"])
    if not deb.is_file() or ic.sha256_file(deb) != p["sha256"]:
        raise SystemExit(f"{deb}: missing or not the locked libfaketime")
    args.out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["dpkg-deb", "-x", str(deb), str(args.out)], check=True)
    libs = sorted(args.out.glob("usr/lib/*/faketime/libfaketime.so.1"))
    if len(libs) != 1:
        raise SystemExit(f"libfaketime.so.1 not found in {deb.name}")
    print(libs[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
