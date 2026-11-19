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
# The name each architecture goes by in upstream file names.
ARCHES = {"x86_64": ("x86_64",), "aarch64": ("aarch64", "arm64")}
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?")
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
        if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", source):
            err("source must be a GitHub repository URL")
        # Every download has to come from this component's own release.
        release = f"{source}/releases/download/v{version}/"

        contract = c.get("contract")
        if contract and not (ROOT / contract).is_file():
            err(f"contract {contract} does not exist")

        sig = c.get("signature", {})
        kind = sig.get("kind")
        if kind == "sigstore":
            for key in ("checksums", "bundle"):
                if not sig.get(key, "").startswith(release):
                    err(f"signature.{key} is not from {release}")
            if sig.get("identity") != f"{source}/.github/workflows/release.yml@refs/tags/v{version}":
                err(f"signature.identity is not {source}'s release workflow at tag v{version}")
            if sig.get("issuer") != "https://token.actions.githubusercontent.com":
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
        urls = set()
        for arch, a in artifacts.items():
            if arch not in ARCHES:
                err(f"unknown architecture {arch!r}")
                continue
            url = a.get("url", "")
            filename = url.rsplit("/", 1)[-1]
            if not url.startswith(release) or "/" in url[len(release):]:
                err(f"{arch} url is not a file in {release}")
            if f"v{version}" not in filename or "linux" not in filename:
                err(f"{arch} url does not name a v{version} Linux build")
            if not any(n in filename for n in ARCHES[arch]):
                err(f"{arch} url does not name an {arch} build")
            for other, names_ in ARCHES.items():
                if other != arch and any(n in filename for n in names_):
                    err(f"{arch} url names an {other} build")
            if url in urls:
                err(f"{arch} url is used for two architectures")
            urls.add(url)
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
