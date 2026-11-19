#!/usr/bin/env python3
"""Check the shape of images/components.toml.

This does not download anything. The image build verifies hashes and
signatures; this catches a malformed or inconsistent manifest in review.
"""
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHES = {"x86_64", "aarch64"}
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?")


def check(path: pathlib.Path) -> list[str]:
    errors = []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    components = data.get("component", [])
    if not components:
        return [f"{path}: no [[component]] entries"]
    names = set()
    for c in components:
        name = c.get("name", "?")
        where = f"component {name!r}"

        def err(msg):
            errors.append(f"{where}: {msg}")

        for key in ("name", "version", "license", "source", "signature", "artifacts"):
            if key not in c:
                err(f"missing {key}")
        if name in names:
            err("listed twice")
        names.add(name)

        version = c.get("version", "")
        if not VERSION.fullmatch(version):
            err(f"version {version!r} is not an exact release version")
        tag = f"/v{version}/"

        if not c.get("source", "").startswith("https://"):
            err("source must be an https URL")
        contract = c.get("contract")
        if contract and not (ROOT / contract).is_file():
            err(f"contract {contract} does not exist")

        sig = c.get("signature", {})
        kind = sig.get("kind")
        if kind == "sigstore":
            for key in ("checksums", "bundle", "identity", "issuer"):
                if not sig.get(key, "").startswith("https://"):
                    err(f"signature.{key} must be an https URL")
            for key in ("checksums", "bundle"):
                if tag not in sig.get(key, ""):
                    err(f"signature.{key} is not from release v{version}")
            if not sig.get("identity", "").endswith(f"@refs/tags/v{version}"):
                err(f"signature.identity is not pinned to tag v{version}")
        elif kind == "none":
            # An unsigned component has to point at the issue that fixes it.
            if not sig.get("tracking", "").startswith("https://github.com/"):
                err("signature.kind = none needs a tracking issue URL")
        else:
            err(f"signature.kind must be sigstore or none, not {kind!r}")

        artifacts = c.get("artifacts", {})
        if not artifacts:
            err("no artifacts")
        for arch, a in artifacts.items():
            if arch not in ARCHES:
                err(f"unknown architecture {arch!r}")
            url = a.get("url", "")
            if not url.startswith("https://") or tag not in url:
                err(f"{arch} url is not an https URL from release v{version}")
            if not SHA256.fullmatch(a.get("sha256", "")):
                err(f"{arch} sha256 is not 64 lowercase hex digits")
    return errors


def main() -> int:
    path = ROOT / "images" / "components.toml"
    errors = check(path)
    for e in errors:
        print(f"{path.relative_to(ROOT)}: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
