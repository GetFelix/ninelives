#!/usr/bin/env python3
"""Run kernel-hardening-checker on the kernel config the image ships.

The kernel job runs the same checker on the config it resolves. This runs it
again from the image job, on the .config of the kernel artifact the image was
built from (the one the UKI's kernel came from, see check_image.py), with the
same policy and exceptions, so a config that changed on the way fails here.

  image_hardening.py --kernel DIR --work DIR [--arch x86_64]
"""
import argparse
import json
import pathlib
import subprocess
import sys
import tarfile
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_kernel_config as kc  # noqa: E402
import image_common as ic  # noqa: E402


def fetch_checker(pin: dict, work: pathlib.Path) -> pathlib.Path:
    """Download the pinned source tarball, check its SHA-256, unpack, return the script."""
    tarball = work / "khc.tar.gz"
    ic.download(pin["url"], tarball)
    digest = ic.sha256_file(tarball)
    if digest != pin["sha256"]:
        raise SystemExit(f"kernel-hardening-checker tarball is {digest}, tools.toml pins {pin['sha256']}")
    dest = work / "khc"
    with tarfile.open(tarball) as t:
        t.extractall(dest, filter="data")
    (top,) = [p for p in dest.iterdir() if p.is_dir()]
    return top / "bin" / "kernel-hardening-checker"


def module_errors(config_text: str) -> list[str]:
    values = kc.parse_config(config_text, ".config")
    # A resolved config states every option; an absent line is not "off".
    if "CONFIG_MODULES" not in values or values["CONFIG_MODULES"] is not kc.UNSET:
        shown = kc.show(values["CONFIG_MODULES"]) if "CONFIG_MODULES" in values else "missing"
        return [f".config: CONFIG_MODULES is {shown}, must be off (0007)"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kernel", type=pathlib.Path, required=True, help="the kernel artifact directory")
    ap.add_argument("--work", type=pathlib.Path, required=True)
    ap.add_argument("--arch", choices=kc.SRCARCH, default="x86_64")
    ap.add_argument("--policy", type=pathlib.Path, default=kc.KERNEL / "policy.toml")
    ap.add_argument("--exceptions", type=pathlib.Path, default=kc.KERNEL / "hardening-exceptions.toml")
    args = ap.parse_args()

    config = args.kernel / ".config"
    args.work.mkdir(parents=True, exist_ok=True)
    pin = ic.load_toml(ic.IMAGES / "tools.toml")["kernel_hardening_checker"]
    checker = fetch_checker(pin, args.work)

    policy = kc.load_policy(args.policy, args.arch)
    cmdline, sysctl = args.work / "cmdline", args.work / "sysctl"
    cmdline.write_text(" ".join(policy.required) + "\n", encoding="utf-8")
    sysctl.write_text("".join(f"{k} = {v}\n" for k, v in policy.sysctl.items()), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, "-I", str(checker), "-m", "json", "-c", str(config), "-l", str(cmdline), "-s", str(sysctl)],
        capture_output=True, text=True, cwd=checker.parent.parent,
    )
    if run.returncode not in (0, 1) or not run.stdout.strip():
        print(f"kernel-hardening-checker failed ({run.returncode}): {run.stderr.strip()}", file=sys.stderr)
        return 1
    (args.work / "hardening.json").write_text(run.stdout, encoding="utf-8")

    exceptions = tomllib.loads(args.exceptions.read_text(encoding="utf-8")).get("exception", [])
    errors = list(policy.errors)
    errors += module_errors(config.read_text(encoding="utf-8"))
    errors += kc.check_hardening(json.loads(run.stdout), exceptions, args.arch)
    for e in errors:
        print(e, file=sys.stderr)
    if not errors:
        print(f"kernel config of the image: kernel-hardening-checker passes ({pin['version']})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
