#!/usr/bin/env python3
"""The image build's fetch phase (docs/components/image.md, "Two phases").

Downloads every input the build needs, checks each against its pin, and lays
the Debian part out as a partial mirror with snapshot.debian.org's own paths,
so apt verifies the signed chain again inside the build. The build phase runs
without network and reads only what this leaves in --out.

  InRelease   SHA-256 from debian.lock, then gpgv against images/keys/debian/
  Packages    SHA-256 from the signed InRelease
  .deb        SHA-256 from debian.lock and from the Packages stanza, both
  mkosi       SHA-256 from images/tools.toml
  cosign      SHA-256 from images/tools.toml, then run offline on each signed
              component's SHA256SUMS and Sigstore bundle against the trusted
              root in images/keys/sigstore/ (SHA-256 from tools.toml)
  Felix, felixd   SHA-256 from images/components.toml; where the component is
              signed, also the line in the verified SHA256SUMS, and the
              bundle's certificate must carry the manifest's identity and issuer

Any mismatch exits non-zero before anything is built.

  fetch_image.py --out DIR          the image build's inputs
  fetch_image.py --out DIR --boot   only the packages the boot test runs on
  fetch_image.py --out DIR --compare   only the packages the comparison job runs on
  fetch_image.py --out DIR --faketime  only libfaketime

The architecture is NL_ARCH (x86_64 if unset), or --target-arch.
"""
import argparse
import json
import lzma
import pathlib
import stat
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import image_common as ic  # noqa: E402
import image_lock  # noqa: E402
import sigstore_verify as sv  # noqa: E402


def fail(msg: str) -> "None":
    raise SystemExit(f"fetch_image: {msg}")


def mirror_path(out: pathlib.Path, snapshot: str, archive: str, rel: str) -> pathlib.Path:
    return out / "mirror" / "archive" / archive / snapshot / rel


def fetch_release(out: pathlib.Path, lock: dict, which: str, arch: str, snapshot: str) -> dict:
    """Verify one release's suites; return {(name, version): stanza} for the lock's packages."""
    keys = ic.IMAGES / "keys" / "debian"
    fprs, errors = ic.load_fingerprints(keys / "fingerprints")
    if errors:
        fail("; ".join(errors))
    part = lock[which]
    wanted = {(p["name"], p["version"]): p for p in part["package"]}
    stanzas: dict[tuple[str, str], dict] = {}
    for suite in part["suite"]:
        archive, name = suite["archive"], suite["name"]
        inrel = mirror_path(out, snapshot, archive, f"dists/{name}/InRelease")
        if not inrel.exists():
            ic.download(image_lock.suite_url(snapshot, archive, name, "InRelease"), inrel)
        digest = ic.sha256_file(inrel)
        if digest != suite["inrelease_sha256"]:
            fail(f"{archive}/{name} InRelease is {digest}, the lock pins {suite['inrelease_sha256']}")
        try:
            text = ic.verify_inrelease(inrel, keys, fprs)
        except ValueError as e:
            fail(str(e))
        rel = f"main/binary-{arch}/Packages.xz"
        want, _ = ic.release_hashes(text)[rel]
        index = mirror_path(out, snapshot, archive, f"dists/{name}/{rel}")
        if not index.exists():
            ic.download(image_lock.suite_url(snapshot, archive, name, rel), index)
        if ic.sha256_file(index) != want:
            fail(f"{archive}/{name} {rel} does not match the signed InRelease")
        for st in ic.parse_deb822(lzma.decompress(index.read_bytes()).decode("utf-8")):
            key = (st["Package"], st["Version"])
            if key in wanted and wanted[key]["archive"] == archive and st["Filename"] == wanted[key]["filename"]:
                stanzas[key] = st
    for key, p in wanted.items():
        st = stanzas.get(key)
        if st is None:
            fail(f"{which}: {key[0]} {key[1]} is not in the signed index of {p['archive']}")
        if st["SHA256"] != p["sha256"]:
            fail(f"{which}: {key[0]} {key[1]}: the index says {st['SHA256']}, the lock {p['sha256']}")
    return stanzas


def fetch_debs(out: pathlib.Path, lock: dict, snapshot: str, sets: tuple[str, ...]) -> int:
    seen = {}
    for which in sets:
        for p in lock[which]["package"]:
            seen[(p["archive"], p["filename"])] = p
    for (archive, filename), p in sorted(seen.items()):
        dest = mirror_path(out, snapshot, archive, filename)
        if not dest.exists():
            ic.download(f"{image_lock.ARCHIVE_URL}/{archive}/{snapshot}/{filename}", dest)
        digest = ic.sha256_file(dest)
        if digest != p["sha256"]:
            dest.unlink()
            fail(f"{p['name']} {p['version']}: downloaded {digest}, the lock pins {p['sha256']}")
    return len(seen)


def fetch_pinned(url: str, sha256: str, dest: pathlib.Path) -> None:
    if not dest.exists():
        ic.download(url, dest)
    digest = ic.sha256_file(dest)
    if digest != sha256:
        dest.unlink()
        fail(f"{dest.name}: downloaded {digest}, pinned {sha256}")


def fetch_verifier(tools: dict, out: pathlib.Path, arch: str) -> tuple[pathlib.Path, pathlib.Path]:
    """The pinned cosign binary for this architecture and the pinned Sigstore trusted root."""
    cs = ic.arch_section(tools["cosign"], arch)
    cosign = out / "tools" / f"cosign-{cs['version']}-{arch}"
    fetch_pinned(cs["url"], cs["sha256"], cosign)
    cosign.chmod(cosign.stat().st_mode | stat.S_IXUSR)
    sg = tools["sigstore"]
    root = ic.ROOT / sg["trusted_root"]
    digest = ic.sha256_file(root)
    if digest != sg["trusted_root_sha256"]:
        fail(f"{sg['trusted_root']} is {digest}, tools.toml pins {sg['trusted_root_sha256']}")
    return cosign, root


def fetch_component(c: dict, arch: str, out: pathlib.Path, verifier: tuple[pathlib.Path, pathlib.Path]) -> dict:
    """Download one component's artifact and check it as its manifest entry says."""
    art = c["artifacts"][arch]
    dest = out / "components" / art["url"].rsplit("/", 1)[1]
    fetch_pinned(art["url"], art["sha256"], dest)
    sig = c["signature"]
    if sig["kind"] == "sigstore":
        cosign, root = verifier
        sdir = out / "components" / "signatures" / c["name"]
        sums, bundle = sdir / "SHA256SUMS", sdir / "SHA256SUMS.sigstore.json"
        # Always fresh: nothing left in a reused --out is trusted without the check.
        for url, path in ((sig["checksums"], sums), (sig["bundle"], bundle)):
            path.unlink(missing_ok=True)
            ic.download(url, path)
        try:
            signed = sums.read_bytes()
            sv.verify_bundle(cosign, root, bundle, sums, sig["identity"], sig["issuer"])
            # Parse the bytes cosign checked, not a later read of the file.
            if sums.read_bytes() != signed:
                raise sv.VerifyError("SHA256SUMS changed during verification")
            sv.check_artifact(sv.parse_sums(signed.decode("utf-8")), dest.name,
                              art["sha256"], ic.sha256_file(dest))
        except sv.VerifyError as e:
            dest.unlink(missing_ok=True)
            fail(f"{c['name']} {c['version']}: {e}")
    elif sig["kind"] != "none":
        dest.unlink(missing_ok=True)
        fail(f"{c['name']}: unknown signature kind {sig['kind']!r}")
    return {"version": c["version"], "file": dest.name, "sha256": art["sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--boot", action="store_true", help="fetch only the boot test's packages")
    parser.add_argument("--compare", action="store_true", help="fetch only the comparison job's packages (diffoscope)")
    parser.add_argument("--faketime", action="store_true", help="fetch only libfaketime")
    parser.add_argument("--target-arch", default=ic.target_arch(), choices=sorted(ic.DEB_ARCH),
                        help="default: NL_ARCH, else x86_64")
    args = parser.parse_args()

    tools = ic.load_toml(ic.IMAGES / "tools.toml")
    lock = ic.load_toml(ic.lock_path(args.target_arch))
    errors = image_lock.check()
    if errors:
        fail("; ".join(errors))
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    snapshot, arch = lock["snapshot"], lock["architecture"]
    if arch != ic.DEB_ARCH[args.target_arch]:
        fail(f"the lock is for {arch}, not {ic.DEB_ARCH[args.target_arch]}")

    chosen = [n for n in ("boot", "compare", "faketime") if getattr(args, n)]
    if len(chosen) > 1:
        fail("--boot, --compare and --faketime are separate runs")
    only = chosen[0] if chosen else None
    # libfaketime is always fetched with the build's inputs: the rebuild uses it.
    sets = (only,) if only else ("root", "tools", "faketime")
    for which in sets:
        fetch_release(out, lock, which, arch, snapshot)
    n = fetch_debs(out, lock, snapshot, sets)
    if only:
        print(f"fetched {n} packages into {out}")
        return 0

    mk = tools["mkosi"]
    fetch_pinned(mk["url"], mk["sha256"], out / "mkosi" / f"mkosi-{mk['version']}.tar.gz")

    # The image job's kernel config check (scripts/image_hardening.py) unpacks
    # this; the bootstrap's CA bundle is gone by the time that job step runs.
    khc = tools["kernel_hardening_checker"]
    fetch_pinned(khc["url"], khc["sha256"], out / "khc" / f"kernel-hardening-checker-{khc['version']}.tar.gz")

    components = ic.load_toml(ic.IMAGES / "components.toml")
    verifier = fetch_verifier(tools, out, args.target_arch)
    # A component with no build for this architecture is not part of its image.
    fetched = {c["name"]: fetch_component(c, args.target_arch, out, verifier)
               for c in components["component"] if args.target_arch in c["artifacts"]}

    (out / "fetched.json").write_text(json.dumps({
        "snapshot": snapshot, "debs": n, "mkosi": mk["sha256"], "components": fetched,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"fetched {n} packages, mkosi {mk['version']}, {len(fetched)} components into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
