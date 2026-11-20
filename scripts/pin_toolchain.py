#!/usr/bin/env python3
"""Pin a Rust release for the init build (images/tools.toml, [rust]).

  pin_toolchain.py VERSION --work DIR            print the pins for VERSION
  pin_toolchain.py VERSION --work DIR --write    also rewrite tools.toml and
                                                 init/rust-toolchain.toml

For every architecture and component it downloads the archive, its published
.sha256 and its .asc signature into DIR, requires the two hashes to agree, and
requires the signature to verify with gpgv against images/keys/rust/ and a
primary key in images/keys/rust/fingerprints. The build never repeats the
signature check: it checks only the SHA-256s this writes, so this runs in the
reviewed bump and leaves its result in the diff.
"""
import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import image_common as ic  # noqa: E402
import init_toolchain as it  # noqa: E402

KEYS = ic.IMAGES / "keys" / "rust"
VERSION = re.compile(r"\d+\.\d+\.\d+")
BEGIN = "# BEGIN rust pins (scripts/pin_toolchain.py)"
END = "# END rust pins"


def verify_signature(archive: pathlib.Path, sig: pathlib.Path, fprs: set[str]) -> None:
    with tempfile.TemporaryDirectory() as d:
        keyring = pathlib.Path(d) / "keyring.gpg"
        keyring.write_bytes(b"".join(ic.dearmor(p.read_text(encoding="ascii")) for p in sorted(KEYS.glob("*.asc"))))
        gpgv = subprocess.run(["gpgv", "--status-fd", "1", "--keyring", str(keyring), str(sig), str(archive)],
                              capture_output=True, text=True)
    signers, bad = ic.valid_signers(gpgv.stdout)
    if gpgv.returncode != 0 or bad:
        raise it.PinError(f"{sig.name}: gpgv failed ({gpgv.returncode}): {' | '.join(bad) or gpgv.stderr.strip()}")
    if not signers & fprs:
        raise it.PinError(f"{sig.name}: signed by {sorted(signers)}, none of them in images/keys/rust/fingerprints")


def pin_component(name: str, version: str, triple: str, work: pathlib.Path, fprs: set[str]) -> dict:
    url = f"{it.DIST}/{name}-{version}-{triple}.tar.xz"
    archive = work / url.rsplit("/", 1)[1]
    sig, published = archive.with_name(archive.name + ".asc"), archive.with_name(archive.name + ".sha256")
    for u, p in ((url, archive), (url + ".asc", sig), (url + ".sha256", published)):
        p.unlink(missing_ok=True)
        ic.download(u, p)
    want = published.read_text(encoding="ascii").split()[0]
    got = ic.sha256_file(archive)
    if want != got or not ic.SHA256.fullmatch(got):
        raise it.PinError(f"{archive.name}: published sha256 {want}, downloaded {got}")
    verify_signature(archive, sig, fprs)
    return {"name": name, "url": url, "sha256": got}


def render(version: str, pins: dict[str, list[dict]]) -> str:
    lines = [BEGIN, "[rust]", f'version = "{version}"', f'triple = "{it.TRIPLE["x86_64"]}"']
    for arch in ("x86_64", "aarch64"):
        if arch != "x86_64":
            lines += ["", "[rust.aarch64]", f'triple = "{it.TRIPLE[arch]}"']
        prefix = "rust" if arch == "x86_64" else "rust.aarch64"
        for c in pins[arch]:
            lines += ["", f"[[{prefix}.component]]", f'name = "{c["name"]}"', f'url = "{c["url"]}"', f'sha256 = "{c["sha256"]}"']
    lines.append(END)
    return "\n".join(lines) + "\n"


def replace_block(text: str, block: str) -> str:
    start, end = text.index(BEGIN), text.index(END) + len(END) + 1
    return text[:start] + block + text[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version")
    parser.add_argument("--work", type=pathlib.Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not VERSION.fullmatch(args.version):
        print(f"{args.version!r} is not an exact x.y.z release", file=sys.stderr)
        return 2
    fprs, errors = ic.load_fingerprints(KEYS / "fingerprints")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    args.work.mkdir(parents=True, exist_ok=True)
    try:
        pins = {arch: [pin_component(n, args.version, it.TRIPLE[arch], args.work, fprs) for n in it.COMPONENTS]
                for arch in ic.DEB_ARCH}
    except (it.PinError, OSError) as e:
        print(f"pin_toolchain: {e}", file=sys.stderr)
        return 1
    block = render(args.version, pins)
    if not args.write:
        print(block, end="")
        return 0
    tools = ic.IMAGES / "tools.toml"
    tools.write_text(replace_block(tools.read_text(encoding="utf-8"), block), encoding="utf-8")
    tc = ic.ROOT / "init" / "rust-toolchain.toml"
    tc.write_text(re.sub(r'channel = "[^"]*"', f'channel = "{args.version}"', tc.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
