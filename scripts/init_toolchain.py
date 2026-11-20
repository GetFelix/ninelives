#!/usr/bin/env python3
"""The inputs of the init build: the Rust toolchain and the crates, all pinned.

  init_toolchain.py fetch --out DIR     download the toolchain archives and the crates
                                        into DIR, each checked against its pin
  init_toolchain.py install --store DIR --prefix DIR
                                        unpack the checked archives into a toolchain
  init_toolchain.py check               init/rust-toolchain.toml and images/tools.toml
                                        name the same Rust version

The archives are pinned by SHA-256 in images/tools.toml ([rust]) because rustup
does not enforce signatures; scripts/pin_toolchain.py checks the published
signatures once, when a bump writes the pins. The crates are the ones in
init/Cargo.lock, each checked against the checksum there and laid out as a
cargo directory source, so the build runs with `--locked --offline`
(docs/components/image.md, "Inputs"). Standard library only: this runs in the
bootstrap container before anything else is trusted.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import image_common as ic  # noqa: E402

CRATES_URL = "https://static.crates.io/crates/{name}/{name}-{version}.crate"
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
COMPONENTS = ("rustc", "cargo", "rust-std")
TRIPLE = {"x86_64": "x86_64-unknown-linux-gnu", "aarch64": "aarch64-unknown-linux-gnu"}
DIST = "https://static.rust-lang.org/dist"


class PinError(Exception):
    pass


def rust_pin(tools: dict, arch: str) -> dict:
    """The toolchain pin as one architecture sees it: version, triple, components."""
    pin = ic.arch_section(tools["rust"], arch)
    got = [c["name"] for c in pin.get("component", [])]
    if got != list(COMPONENTS):
        raise PinError(f"tools.toml: rust components for {arch} are {got}, expected {list(COMPONENTS)}")
    for c in pin["component"]:
        want = f"{DIST}/{c['name']}-{pin['version']}-{pin['triple']}.tar.xz"
        if c["url"] != want:
            raise PinError(f"tools.toml: {c['name']} url for {arch} is {c['url']}, expected {want}")
        if not ic.SHA256.fullmatch(c["sha256"]):
            raise PinError(f"tools.toml: {c['name']} sha256 for {arch} is not 64 lowercase hex digits")
    if pin["triple"] != TRIPLE[arch]:
        raise PinError(f"tools.toml: rust triple for {arch} is {pin['triple']}, expected {TRIPLE[arch]}")
    return pin


def check_versions(tools: dict, toolchain_file: pathlib.Path) -> list[str]:
    """The channel in rust-toolchain.toml is the version tools.toml pins, for every architecture."""
    channel = ic.load_toml(toolchain_file)["toolchain"]["channel"]
    errors = []
    for arch in ic.DEB_ARCH:
        try:
            pin = rust_pin(tools, arch)
        except PinError as e:
            errors.append(str(e))
            continue
        if pin["version"] != channel:
            errors.append(f"{toolchain_file.name} names Rust {channel}, tools.toml pins {pin['version']} for {arch}")
    return errors


def fetch_checked(url: str, sha256: str, dest: pathlib.Path) -> None:
    """Download url to dest and check its SHA-256. A bad file is removed, never kept."""
    dest.unlink(missing_ok=True)
    ic.download(url, dest)
    digest = ic.sha256_file(dest)
    if digest != sha256:
        dest.unlink()
        raise PinError(f"{dest.name}: downloaded {digest}, pinned {sha256}")


def fetch_toolchain(pin: dict, store: pathlib.Path) -> list[pathlib.Path]:
    out = []
    for c in pin["component"]:
        dest = store / "rust" / c["url"].rsplit("/", 1)[1]
        fetch_checked(c["url"], c["sha256"], dest)
        out.append(dest)
    return out


def install_toolchain(pin: dict, store: pathlib.Path, prefix: pathlib.Path) -> None:
    """Unpack each archive and run its own installer into prefix. Checks the hash again first."""
    for c in pin["component"]:
        archive = store / "rust" / c["url"].rsplit("/", 1)[1]
        if ic.sha256_file(archive) != c["sha256"]:
            raise PinError(f"{archive.name} is not the pinned archive")
        with tempfile.TemporaryDirectory() as d, tarfile.open(archive, "r:xz") as tar:
            tar.extractall(d, filter="data")
            (src,) = pathlib.Path(d).iterdir()
            subprocess.run(["sh", str(src / "install.sh"), f"--prefix={prefix}", "--disable-ldconfig"],
                           check=True, stdout=subprocess.DEVNULL)


def locked_crates(lock_text: str) -> list[dict]:
    """The registry packages of a Cargo.lock: name, version, checksum. Anything else is refused."""
    out = []
    for p in tomllib.loads(lock_text).get("package", []):
        source = p.get("source")
        if source is None:
            continue  # the workspace's own crate
        if source != REGISTRY or not ic.SHA256.fullmatch(p.get("checksum", "")):
            raise PinError(f"Cargo.lock: {p['name']} {p['version']} is not a crates.io package with a checksum")
        out.append({"name": p["name"], "version": p["version"], "checksum": p["checksum"]})
    return out


def unpack_crate(crate: pathlib.Path, vendor: pathlib.Path, name: str, version: str, checksum: str) -> None:
    """Lay one .crate out as a cargo directory source. Cargo checks `package` against Cargo.lock."""
    dest = vendor / f"{name}-{version}"
    prefix = f"{name}-{version}/"
    with tarfile.open(crate, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.name.startswith(prefix):
                raise PinError(f"{crate.name}: {m.name} is outside {prefix}")
            if m.isdir():
                continue
            if not m.isfile() or ".." in pathlib.PurePosixPath(m.name).parts:
                raise PinError(f"{crate.name}: {m.name} is not a plain file")
            target = dest / m.name[len(prefix):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(m).read())
            target.chmod(0o755 if m.mode & 0o100 else 0o644)
    (dest / ".cargo-checksum.json").write_text(json.dumps({"files": {}, "package": checksum}), encoding="utf-8")


def fetch_crates(lock_text: str, store: pathlib.Path) -> int:
    crates = locked_crates(lock_text)
    vendor = store / "vendor"
    for c in crates:
        name, version = c["name"], c["version"]
        crate = store / "crates" / f"{name}-{version}.crate"
        fetch_checked(CRATES_URL.format(name=name, version=version), c["checksum"], crate)
        unpack_crate(crate, vendor, name, version, c["checksum"])
    return len(crates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", type=pathlib.Path, required=True)
    i = sub.add_parser("install")
    i.add_argument("--store", type=pathlib.Path, required=True)
    i.add_argument("--prefix", type=pathlib.Path, required=True)
    sub.add_parser("check")
    args = parser.parse_args()
    tools = ic.load_toml(ic.IMAGES / "tools.toml")
    arch = ic.target_arch()
    try:
        if args.cmd == "check":
            errors = check_versions(tools, ic.ROOT / "init" / "rust-toolchain.toml")
            for e in errors:
                print(e, file=sys.stderr)
            return 1 if errors else 0
        errors = check_versions(tools, ic.ROOT / "init" / "rust-toolchain.toml")
        if errors:
            raise PinError("; ".join(errors))
        pin = rust_pin(tools, arch)
        if args.cmd == "fetch":
            fetch_toolchain(pin, args.out)
            n = fetch_crates((ic.ROOT / "init" / "Cargo.lock").read_text(encoding="utf-8"), args.out)
            print(f"fetched Rust {pin['version']} ({arch}) and {n} crates into {args.out}")
        else:
            install_toolchain(pin, args.store, args.prefix)
            print(f"installed Rust {pin['version']} into {args.prefix}")
    except PinError as e:
        print(f"init_toolchain: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
