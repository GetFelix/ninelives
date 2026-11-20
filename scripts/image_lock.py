#!/usr/bin/env python3
"""Generate or check images/debian.lock (docs/components/image.md, "Inputs").

  image_lock.py generate   resolve the package sets against the pinned snapshot
                           and rewrite the lock of NL_ARCH
  image_lock.py check      check every architecture's lock: its shape and its
                           consistency with images/tools.toml and
                           images/packages.toml, offline
  image_lock.py tools      print the tools set as name=version, one per line
  image_lock.py boot       the same for the set the boot test runs on
  image_lock.py compare    the same for the set the build comparison runs on
  image_lock.py boot-pin K print one key of the boot test's pins for NL_ARCH (qemu, firmware_code, firmware_vars)
  image_lock.py keyring F  write the pinned archive keys to F as a binary keyring

There is one lock per architecture: images/debian.lock for x86_64 and
images/debian-aarch64.lock for aarch64. NL_ARCH picks the one a command reads
or writes (x86_64 if unset); `check` covers all of them.

The lock holds, for the root, the build tools, the boot test's tools, the
comparison's tools, libfaketime, the bootstrap packages and the init's build tools
(scripts/bootstrap.sh installs the last two into the base container) separately, every
package by name, version, SHA-256 and pool path, plus the SHA-256 of each
suite's InRelease. Resolution follows apt's rules closely enough for a
reviewed set; the fetch phase then fails if apt wants anything the lock does
not name.
"""
import argparse
import lzma
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import image_common as ic  # noqa: E402

SNAPSHOT = re.compile(r"\d{8}T\d{6}Z")
NAME = re.compile(r"[a-z0-9][a-z0-9+.-]*")
PRIORITY = {"required": 0, "important": 1, "standard": 2, "optional": 3, "extra": 4}
ARCHIVE_URL = "http://snapshot.debian.org/archive"
SETS = ("root", "tools", "boot", "compare", "faketime", "bootstrap", "build")


def suites_for(release: str) -> list[tuple[str, str]]:
    return [("debian", release), ("debian", f"{release}-updates"), ("debian-security", f"{release}-security")]


def load_pins() -> dict:
    tools = ic.load_toml(ic.IMAGES / "tools.toml")
    packages = ic.load_toml(ic.IMAGES / "packages.toml")
    return {"tools": tools, "packages": packages}


def suite_url(snapshot: str, archive: str, suite: str, path: str) -> str:
    return f"{ARCHIVE_URL}/{archive}/{snapshot}/dists/{suite}/{path}"


def load_universe(snapshot: str, arch: str, release: str, work: pathlib.Path):
    """name -> stanza (highest version across the release's suites), and each suite's InRelease hash."""
    keys = ic.IMAGES / "keys" / "debian"
    fprs, errors = ic.load_fingerprints(keys / "fingerprints")
    if errors:
        raise SystemExit("\n".join(errors))
    universe: dict[str, dict] = {}
    inrelease = {}
    for archive, suite in suites_for(release):
        path = work / archive / suite / "InRelease"
        ic.download(suite_url(snapshot, archive, suite, "InRelease"), path)
        text = ic.verify_inrelease(path, keys, fprs)
        inrelease[(archive, suite)] = ic.sha256_file(path)
        rel = f"main/binary-{arch}/Packages.xz"
        want, size = ic.release_hashes(text)[rel]
        pkgs = work / archive / suite / "Packages.xz"
        ic.download(suite_url(snapshot, archive, suite, rel), pkgs)
        if ic.sha256_file(pkgs) != want:
            raise SystemExit(f"{archive}/{suite}: {rel} does not match InRelease")
        for name, st in ic.parse_packages(lzma.decompress(pkgs.read_bytes()).decode("utf-8")).items():
            st["_archive"] = archive
            if name not in universe or ic.vercmp(st["Version"], universe[name]["Version"]) > 0:
                universe[name] = st
    return universe, inrelease


def closure(universe: dict, roots: list[str], include_essential: bool) -> list[str]:
    providers: dict[str, list[str]] = {}
    for name, st in universe.items():
        for v in ic.provides(st):
            providers.setdefault(v, []).append(name)
    chosen: dict[str, None] = {}
    queue = list(roots)
    if include_essential:
        queue += sorted(n for n, st in universe.items() if st.get("Essential") == "yes")
    for r in roots:
        if r not in universe:
            raise SystemExit(f"{r}: not in the snapshot")
    while queue:
        name = queue.pop(0)
        if name in chosen:
            continue
        chosen[name] = None
        st = universe[name]
        for field in ("Pre-Depends", "Depends"):
            for group in ic.dep_alternatives(st.get(field, "")):
                if any(a in chosen for a in group):
                    continue
                pick = next((a for a in group if a in universe), None)
                if pick is None:
                    for a in group:
                        # apt prefers the provider with the higher priority.
                        cands = sorted(providers.get(a, []), key=lambda n: (PRIORITY.get(universe[n].get("Priority"), 9), n))
                        done = [c for c in cands if c in chosen]
                        if done:
                            pick = done[0]
                            break
                        if cands:
                            pick = cands[0]
                            break
                if pick is None:
                    raise SystemExit(f"{name}: nothing provides {' | '.join(group)}")
                queue.append(pick)
    return sorted(chosen)


def entries(universe: dict, names: list[str]) -> list[dict]:
    out = []
    for n in names:
        st = universe[n]
        out.append({
            "name": n,
            "version": st["Version"],
            "sha256": st["SHA256"],
            "archive": st["_archive"],
            "filename": st["Filename"],
            "architecture": st["Architecture"],
            "source": (st.get("Source") or n).split(" ")[0],
        })
    return out


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(snapshot: str, arch: str, sets: dict) -> str:
    lines = [
        "# Every Debian package the image build uses, by name, version and SHA-256,",
        "# for the root, the build tools, the boot test's tools, the comparison's tools, libfaketime, the bootstrap",
        "# step's packages and the init's build tools separately. Written by",
        "# scripts/image_lock.py; CI regenerates it and fails on a difference, so",
        "# a hand edit cannot add a package resolution would not. Change it only in",
        "# a reviewed pull request that touches pin files and nothing else",
        "# (docs/components/image.md, \"How a bump flows through review\").",
        "",
        f"snapshot = {q(snapshot)}",
        f"architecture = {q(arch)}",
    ]
    for which in SETS:
        s = sets[which]
        lines += ["", f"[{which}]", f"release = {q(s['release'])}"]
        for (archive, suite), digest in sorted(s["inrelease"].items()):
            lines += ["", f"[[{which}.suite]]", f"archive = {q(archive)}", f"name = {q(suite)}", f"inrelease_sha256 = {q(digest)}"]
        for e in s["packages"]:
            lines += ["", f"[[{which}.package]]"] + [f"{k} = {q(e[k])}" for k in ("name", "version", "sha256", "archive", "filename", "architecture", "source")]
    return "\n".join(lines) + "\n"


def set_packages(tools: dict, which: str, target: str) -> list[str]:
    return ic.arch_section(tools[which], target)["packages"]


def generate(output: pathlib.Path | None = None) -> None:
    target = ic.target_arch()
    output = output or ic.lock_path(target)
    pins = load_pins()
    tools = pins["tools"]
    snapshot, arch = tools["debian"]["snapshot"], ic.DEB_ARCH[target]
    keep = [p["name"] for p in pins["packages"]["package"]]
    sets = {}
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d)
        for which, roots, essential in (("root", keep, True), *((w, set_packages(tools, w, target), False) for w in SETS[1:])):
            release = tools[which]["release"]
            universe, inrelease = load_universe(snapshot, arch, release, work / which)
            names = closure(universe, ["base-files", *roots] if essential else roots, essential)
            sets[which] = {"release": release, "inrelease": inrelease, "packages": entries(universe, names)}
    output.write_text(render(snapshot, arch, sets), encoding="utf-8")


def check_lock(target: str, tools: dict, keep: list[dict]) -> list[str]:
    errors = []
    path = ic.lock_path(target)
    lock = ic.load_toml(path)
    name = path.name
    deb_arch = ic.DEB_ARCH[target]
    snapshot = tools["debian"]["snapshot"]
    if lock.get("snapshot") != snapshot:
        errors.append(f"{name}: snapshot is not tools.toml's {snapshot}")
    if lock.get("architecture") != deb_arch:
        errors.append(f"{name}: architecture is not {deb_arch}, the one for {target}")
    for which in SETS:
        part = lock.get(which, {})
        if part.get("release") != tools[which]["release"]:
            errors.append(f"{name}: {which} release is not tools.toml's {tools[which]['release']}")
        suites = {(s["archive"], s["name"]) for s in part.get("suite", [])}
        if suites != set(suites_for(tools[which]["release"])):
            errors.append(f"{name}: {which} suites are not those of release {tools[which]['release']}")
        for s in part.get("suite", []):
            if not ic.SHA256.fullmatch(s.get("inrelease_sha256", "")):
                errors.append(f"{name}: {which} suite {s.get('name')} has a bad inrelease_sha256")
        names = []
        for p in part.get("package", []):
            where = f"{name}: {which} package {p.get('name')!r}"
            names.append(p.get("name"))
            if not NAME.fullmatch(p.get("name", "")):
                errors.append(f"{where}: bad name")
            if not ic.SHA256.fullmatch(p.get("sha256", "")):
                errors.append(f"{where}: sha256 is not 64 lowercase hex digits")
            fn = p.get("filename", "")
            if not re.fullmatch(r"pool/(?:updates/)?main/[a-z0-9+.-]+/[a-z0-9+.-]+/[A-Za-z0-9+._~%-]+\.deb", fn):
                errors.append(f"{where}: bad pool path {fn!r}")
            if p.get("architecture") not in (deb_arch, "all"):
                errors.append(f"{where}: bad architecture")
            if p.get("archive") not in ("debian", "debian-security"):
                errors.append(f"{where}: bad archive")
        if names != sorted(names) or len(names) != len(set(names)):
            errors.append(f"{name}: {which} packages are not sorted and unique")
        if not names:
            errors.append(f"{name}: {which} has no packages")
    root = {p["name"] for p in lock.get("root", {}).get("package", [])}
    for p in keep:
        if p["name"] not in root:
            errors.append(f"packages.toml: {p['name']} is not in {name}'s root set")
    for which in SETS[1:]:
        in_lock = {p["name"] for p in lock.get(which, {}).get("package", [])}
        for n in set_packages(tools, which, target):
            if n not in in_lock:
                errors.append(f"tools.toml: {which} package {n} is not in {name}'s {which} set")
    return errors


def check() -> list[str]:
    tools = ic.load_toml(ic.IMAGES / "tools.toml")
    keep = ic.load_toml(ic.IMAGES / "packages.toml").get("package", [])
    errors = []
    snapshot = tools["debian"]["snapshot"]
    if not SNAPSHOT.fullmatch(snapshot):
        errors.append(f"tools.toml: snapshot {snapshot!r} is not a snapshot.debian.org timestamp")
    for target in ic.DEB_ARCH:
        errors += check_lock(target, tools, keep)
    # The workflow's container is the pinned bootstrap image, by the same digest.
    ci = (ic.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    digest = tools["bootstrap"]["image"].split("@", 1)[1]
    if f"image: debian@{digest}" not in ci:
        errors.append("ci.yml: the image job's container is not the one tools.toml pins")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["generate", "check", "tools", "boot", "compare", "keyring", "boot-pin"])
    parser.add_argument("target", nargs="?", type=pathlib.Path, help="generate: the file to write; keyring: the keyring file; boot-pin: the key")
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.target)
        return 0
    if args.command in ("tools", "boot", "compare"):
        lock = ic.load_toml(ic.lock_path())
        print("\n".join(f"{p['name']}={p['version']}" for p in lock[args.command]["package"]))
        return 0
    if args.command == "boot-pin":
        pins = ic.arch_section(ic.load_toml(ic.IMAGES / "tools.toml")["boot"], ic.target_arch())
        print(pins[str(args.target)])
        return 0
    if args.command == "keyring":
        keys = ic.IMAGES / "keys" / "debian"
        args.target.write_bytes(b"".join(ic.dearmor(p.read_text(encoding="ascii")) for p in sorted(keys.glob("*.asc"))))
        return 0
    errors = check()
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
