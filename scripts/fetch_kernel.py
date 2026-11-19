#!/usr/bin/env python3
"""Download the pinned kernel tarball and verify it (kernel/VERSION).

Order matters: the SHA-256 in kernel/VERSION is checked first, then the
upstream PGP signature over the decompressed tarball with gpgv against the
keys in kernel/keys/. The signature counts only if gpgv reports VALIDSIG for
a primary key listed in kernel/keys/fingerprints. Both checks, because a hash
alone trusts the reviewer and a signature alone accepts any release the
maintainers signed. Any failure exits non-zero; files already in --out are
verified again, never trusted.

  fetch_kernel.py --check                    check kernel/VERSION and kernel/keys/ only
  fetch_kernel.py --check --base DIR         also compare with the target branch's
                                             kernel/ in DIR: no downgrade, and the
                                             keys never change with the pin
  fetch_kernel.py --out DIR                  download into DIR (reusing files there) and verify
  fetch_kernel.py --out DIR --bump 6.18.65   verify a new release's signature, then
                                             rewrite kernel/VERSION with its hash
"""
import argparse
import base64
import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL = ROOT / "kernel"
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
FINGERPRINT = re.compile(r"[0-9A-F]{40}")
# The pinned long-term line. Moving to the next one changes this, in a pull
# request with nothing else in it (kernel.md, "The release and how it moves").
SERIES = (6, 18)


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def load_pin(path: pathlib.Path) -> tuple[dict, list[str]]:
    pin = tomllib.loads(path.read_text(encoding="utf-8"))
    errors = []
    m = VERSION.fullmatch(pin.get("version", ""))
    if not m:
        return pin, [f"{path.name}: version {pin.get('version')!r} is not an exact x.y.z release"]
    v = pin["version"]
    if version_tuple(v)[:2] != SERIES:
        errors.append(f"{path.name}: {v} is not in the pinned {SERIES[0]}.{SERIES[1]}.y series")
    base = f"https://cdn.kernel.org/pub/linux/kernel/v{m.group(1)}.x/linux-{v}"
    if pin.get("url") != f"{base}.tar.xz":
        errors.append(f"{path.name}: url must be {base}.tar.xz")
    if pin.get("signature") != f"{base}.tar.sign":
        errors.append(f"{path.name}: signature must be {base}.tar.sign")
    if not re.fullmatch(r"[0-9a-f]{64}", pin.get("sha256", "")):
        errors.append(f"{path.name}: sha256 is not 64 lowercase hex digits")
    return pin, errors


def load_fingerprints(path: pathlib.Path) -> tuple[set[str], list[str]]:
    fprs, errors = set(), []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fpr = line.split()[0]
        if not FINGERPRINT.fullmatch(fpr):
            errors.append(f"{path.name}:{n}: {fpr!r} is not a 40-digit uppercase fingerprint")
        fprs.add(fpr)
    if not fprs:
        errors.append(f"{path.name}: no fingerprints")
    return fprs, errors


def key_files(keys: pathlib.Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(keys.iterdir()) if p.is_file()} if keys.is_dir() else {}


def check_against_base(kernel: pathlib.Path, base: pathlib.Path) -> list[str]:
    """Compare the pin with the target branch's kernel/ directory in base.

    A bump and a key change in one pull request would let a new key vouch for
    the release it arrives with, so each has to be reviewed on its own.
    """
    if not (base / "VERSION").is_file():
        return []
    errors = []
    old = tomllib.loads((base / "VERSION").read_text(encoding="utf-8")).get("version", "")
    new = tomllib.loads((kernel / "VERSION").read_text(encoding="utf-8")).get("version", "")
    if VERSION.fullmatch(old) and VERSION.fullmatch(new) and version_tuple(new) < version_tuple(old):
        errors.append(f"VERSION: {new} is older than {old} on the target branch")
    pin_changed = (base / "VERSION").read_bytes() != (kernel / "VERSION").read_bytes()
    if pin_changed and key_files(base / "keys") != key_files(kernel / "keys"):
        errors.append("kernel/keys/ and kernel/VERSION both change; change the keys in their own pull request")
    return errors


def crc24(data: bytes) -> int:
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def dearmor(text: str) -> bytes:
    """One ASCII-armored public key block to binary, checking its CRC."""
    lines = text.strip().splitlines()
    if lines[0] != "-----BEGIN PGP PUBLIC KEY BLOCK-----" or lines[-1] != "-----END PGP PUBLIC KEY BLOCK-----":
        raise ValueError("not a single armored public key block")
    body = lines[1:-1]
    # Armor headers end at the first empty line.
    body = body[body.index("") + 1:] if "" in body else body
    crc = None
    if body and body[-1].startswith("="):
        crc = int.from_bytes(base64.b64decode(body.pop()[1:]), "big")
    data = base64.b64decode("".join(body), validate=True)
    if crc is not None and crc24(data) != crc:
        raise ValueError("armor checksum mismatch")
    return data


def valid_signers(status: str) -> tuple[set[str], list[str]]:
    """Primary fingerprints from gpgv's VALIDSIG lines, and any bad status."""
    signers, bad = set(), []
    for line in status.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "[GNUPG:]":
            continue
        if parts[1] == "VALIDSIG" and len(parts) >= 12:
            signers.add(parts[11])
        elif parts[1] in ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY"):
            bad.append(line)
    return signers, bad


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: pathlib.Path) -> None:
    if dest.exists():
        return
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, part.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    part.rename(dest)


def verify(pin: dict, tarball: pathlib.Path, sig: pathlib.Path, keys: pathlib.Path, fprs: set[str]) -> list[str]:
    digest = sha256(tarball)
    if digest != pin["sha256"]:
        return [f"{tarball.name}: sha256 {digest} is not the pinned {pin['sha256']}"]
    return verify_signature(tarball, sig, keys, fprs)


def verify_signature(tarball: pathlib.Path, sig: pathlib.Path, keys: pathlib.Path, fprs: set[str]) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        keyring = pathlib.Path(d) / "keyring.gpg"
        keyring.write_bytes(b"".join(dearmor(p.read_text(encoding="ascii")) for p in sorted(keys.glob("*.asc"))))
        # The signature covers the uncompressed tar, so it is streamed through xz.
        xz = subprocess.Popen(["xz", "-dc", str(tarball)], stdout=subprocess.PIPE)
        gpgv = subprocess.run(
            ["gpgv", "--status-fd", "1", "--keyring", str(keyring), str(sig), "-"],
            stdin=xz.stdout, capture_output=True, text=True,
        )
        xz.stdout.close()
        if xz.wait() != 0:
            return [f"{tarball.name}: xz failed to decompress"]
    signers, bad = valid_signers(gpgv.stdout)
    if gpgv.returncode != 0 or bad:
        return [f"{sig.name}: gpgv failed ({gpgv.returncode}): {' | '.join(bad) or gpgv.stderr.strip()}"]
    if not signers & fprs:
        return [f"{sig.name}: signed by {sorted(signers)}, none of them in kernel/keys/fingerprints"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="check the pin files only, offline")
    group.add_argument("--out", type=pathlib.Path, help="directory to download into")
    parser.add_argument("--bump", metavar="VERSION", help="with --out: pin this release instead")
    parser.add_argument("--base", type=pathlib.Path, help="with --check: the target branch's kernel/ directory")
    args = parser.parse_args()
    if args.bump and not args.out:
        parser.error("--bump needs --out")
    if args.base and not args.check:
        parser.error("--base needs --check")

    pin, errors = load_pin(KERNEL / "VERSION")
    fprs, more = load_fingerprints(KERNEL / "keys" / "fingerprints")
    errors += more
    for p in sorted((KERNEL / "keys").glob("*.asc")):
        try:
            dearmor(p.read_text(encoding="ascii"))
        except ValueError as e:
            errors.append(f"keys/{p.name}: {e}")
    if args.base:
        errors += check_against_base(KERNEL, args.base)
    if not errors and args.bump:
        m = VERSION.fullmatch(args.bump)
        if not m:
            parser.error(f"{args.bump!r} is not an exact x.y.z release")
        if version_tuple(args.bump)[:2] != SERIES:
            parser.error(f"{args.bump} is not in the pinned {SERIES[0]}.{SERIES[1]}.y series")
        if version_tuple(args.bump) <= version_tuple(pin["version"]):
            parser.error(f"{args.bump} is not newer than the pinned {pin['version']}")
        base = f"https://cdn.kernel.org/pub/linux/kernel/v{m.group(1)}.x/linux-{args.bump}"
        args.out.mkdir(parents=True, exist_ok=True)
        tarball, sig = args.out / f"linux-{args.bump}.tar.xz", args.out / f"linux-{args.bump}.tar.sign"
        download(f"{base}.tar.xz", tarball)
        download(f"{base}.tar.sign", sig)
        # The new hash is trusted only because the pinned key signed the tarball;
        # the reviewer of the bump checks it against kernel.org's sha256sums.asc.
        errors = verify_signature(tarball, sig, KERNEL / "keys", fprs)
        if not errors:
            text = (KERNEL / "VERSION").read_text(encoding="utf-8")
            for key, value in (("version", args.bump), ("url", f"{base}.tar.xz"),
                               ("signature", f"{base}.tar.sign"), ("sha256", sha256(tarball))):
                text = re.sub(rf'(?m)^{key} = ".*"$', f'{key} = "{value}"', text)
            (KERNEL / "VERSION").write_text(text, encoding="utf-8")
            print(f"kernel/VERSION now pins {args.bump}")
    elif not errors and args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        tarball = args.out / pin["url"].rsplit("/", 1)[1]
        sig = args.out / pin["signature"].rsplit("/", 1)[1]
        download(pin["url"], tarball)
        download(pin["signature"], sig)
        errors = verify(pin, tarball, sig, KERNEL / "keys", fprs)
        if not errors:
            print(tarball)
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
