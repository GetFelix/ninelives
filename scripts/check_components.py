#!/usr/bin/env python3
"""Check the shape of images/components.toml.

This does not download anything. The image build verifies hashes and
signatures; this catches a malformed or inconsistent manifest in review.

With --base, also compares against the manifest on the target branch and
refuses a change that drops a component's signature.
"""
import argparse
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The name each architecture goes by in upstream file names.
ARCHES = {"x86_64": ("x86_64",), "aarch64": ("aarch64", "arm64")}
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?")
# Upstreams NineLives takes components from. Adding one is a reviewed change.
OWNERS = {"GetFelix"}
ISSUE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+")


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
        source = c.get("source", "").rstrip("/")
        m = re.fullmatch(r"https://github\.com/([\w.-]+)/[\w.-]+", source)
        if not m or m.group(1) not in OWNERS:
            err(f"source must be a GitHub repository owned by one of {sorted(OWNERS)}")
        # Every download has to come from this component's own release.
        release = f"{source}/releases/download/v{version}/"

        contract = c.get("contract")
        if contract and not (ROOT / contract).is_file():
            err(f"contract {contract} does not exist")

        sig = c.get("signature", {})
        kind = sig.get("kind")
        if kind == "sigstore":
            # The fetch verifies the bundle against these, so none may be absent or empty.
            for key in ("checksums", "bundle", "identity", "issuer"):
                if not isinstance(sig.get(key), str) or not sig[key]:
                    err(f"signature.kind = sigstore needs signature.{key}")
            for key in ("checksums", "bundle"):
                if sig.get(key) and not sig[key].startswith(release):
                    err(f"signature.{key} is not from {release}")
            if sig.get("identity") and sig["identity"] != f"{source}/.github/workflows/release.yml@refs/tags/v{version}":
                err(f"signature.identity is not {source}'s release workflow at tag v{version}")
            if sig.get("issuer") and sig["issuer"] != "https://token.actions.githubusercontent.com":
                err("signature.issuer is not GitHub Actions")
        elif kind == "none":
            # An unsigned component has to point at the issue that fixes it.
            if not ISSUE.fullmatch(sig.get("tracking", "")):
                err("signature.kind = none needs a tracking issue URL")
        else:
            err(f"signature.kind must be sigstore or none, not {kind!r}")

        artifacts = c.get("artifacts", {})
        if not artifacts:
            err("no artifacts")
        urls, hashes = set(), set()
        for arch, a in artifacts.items():
            if arch not in ARCHES:
                err(f"unknown architecture {arch!r}")
                continue
            url = a.get("url", "")
            filename = url.rsplit("/", 1)[-1]
            if not url.startswith(release) or "/" in url[len(release):]:
                err(f"{arch} url is not a file in {release}")
            if not filename.startswith(f"{name}-v{version}-") or not filename.endswith(".tar.gz"):
                err(f"{arch} url is not a {name}-v{version}-*.tar.gz archive")
            if "linux" not in filename:
                err(f"{arch} url does not name a Linux build")
            if not any(n in filename for n in ARCHES[arch]):
                err(f"{arch} url does not name an {arch} build")
            for other, names_ in ARCHES.items():
                if other != arch and any(n in filename for n in names_):
                    err(f"{arch} url names an {other} build")
            if url in urls:
                err(f"{arch} url is used for two architectures")
            urls.add(url)
            digest = a.get("sha256", "")
            if not SHA256.fullmatch(digest):
                err(f"{arch} sha256 is not 64 lowercase hex digits")
            if digest in hashes:
                err(f"{arch} sha256 is used for two architectures")
            hashes.add(digest)
    return errors


def check_against_base(path: pathlib.Path, base: pathlib.Path) -> list[str]:
    def kinds(p):
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        return {c.get("name"): c.get("signature", {}).get("kind") for c in data.get("component", [])}

    # A signed component may not lose its signature, be renamed or be removed
    # without the check noticing; removing one needs this script changed too.
    old, new = kinds(base), kinds(path)
    errors = []
    for n, kind in old.items():
        if kind != "sigstore":
            continue
        if n not in new:
            errors.append(f"component {n!r}: signed component removed or renamed")
        elif new[n] != "sigstore":
            errors.append(f"component {n!r}: signature dropped from sigstore to {new[n]!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=pathlib.Path, help="the manifest on the target branch")
    args = parser.parse_args()
    path = ROOT / "images" / "components.toml"
    errors = check(path)
    if args.base:
        errors += check_against_base(path, args.base)
    for e in errors:
        print(f"{path.relative_to(ROOT)}: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
