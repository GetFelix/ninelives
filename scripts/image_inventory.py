#!/usr/bin/env python3
"""Write NAME.inventory.json: everything the image was made from (docs/components/image.md, "Outputs")."""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import image_common as ic  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", type=pathlib.Path, required=True)
    ap.add_argument("--store", type=pathlib.Path, required=True)
    ap.add_argument("--kernel-dir", type=pathlib.Path, required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--source-date-epoch", type=int, required=True)
    ap.add_argument("--output", type=pathlib.Path, required=True)
    args = ap.parse_args()
    stage = json.loads((args.meta / "stage.json").read_text())
    kernel = ic.load_toml(ic.ROOT / "kernel" / "VERSION")
    tools = ic.load_toml(ic.IMAGES / "tools.toml")
    inv = {
        "commit": args.commit,
        "source_date_epoch": args.source_date_epoch,
        "snapshot": stage["snapshot"],
        "packages": stage["packages"],
        "components": stage["components"],
        "kernel": {
            "version": kernel["version"],
            "tarball_sha256": kernel["sha256"],
            "image_sha256": ic.sha256_file(args.kernel_dir / "bzImage"),
            "config_sha256": ic.sha256_file(args.kernel_dir / ".config"),
        },
        "mkosi": {"version": tools["mkosi"]["version"], "sha256": tools["mkosi"]["sha256"]},
        "bootstrap_image": tools["bootstrap"]["image"],
    }
    args.output.write_text(json.dumps(inv, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
