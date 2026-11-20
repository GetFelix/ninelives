"""Unit tests for scripts/init_toolchain.py and scripts/pin_toolchain.py.

Run from the repository root: python3 -m unittest discover -s scripts/tests
Nothing here touches the network: downloads are replaced by files written in
a temporary directory.
"""
import hashlib
import io
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import image_common as ic  # noqa: E402
import init_toolchain as it  # noqa: E402
import pin_toolchain as pt  # noqa: E402

TOOLS = ic.load_toml(ic.IMAGES / "tools.toml")
TOOLCHAIN_FILE = ic.ROOT / "init" / "rust-toolchain.toml"


def fake_download(content: bytes):
    def download(url, dest, attempts=5):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    return download


class Pins(unittest.TestCase):
    def test_rust_toolchain_toml_names_the_pinned_version(self):
        self.assertEqual(it.check_versions(TOOLS, TOOLCHAIN_FILE), [])

    def test_a_version_that_differs_is_reported_for_each_architecture(self):
        with tempfile.TemporaryDirectory() as d:
            other = pathlib.Path(d) / "rust-toolchain.toml"
            other.write_text('[toolchain]\nchannel = "1.0.0"\n')
            errors = it.check_versions(TOOLS, other)
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(all("1.0.0" in e for e in errors))

    def test_each_architecture_has_its_own_archives_by_hash(self):
        seen = set()
        for arch in ic.DEB_ARCH:
            pin = it.rust_pin(TOOLS, arch)
            self.assertEqual([c["name"] for c in pin["component"]], list(it.COMPONENTS))
            for c in pin["component"]:
                self.assertIn(pin["triple"], c["url"])
                seen.add(c["sha256"])
        self.assertEqual(len(seen), 6)

    def test_a_url_that_does_not_match_the_version_is_refused(self):
        tools = json.loads(json.dumps(TOOLS))
        tools["rust"]["component"][0]["url"] = "https://example.com/rustc.tar.xz"
        with self.assertRaises(it.PinError):
            it.rust_pin(tools, "x86_64")

    def test_a_missing_component_is_refused(self):
        tools = json.loads(json.dumps(TOOLS))
        del tools["rust"]["aarch64"]["component"][2]
        with self.assertRaises(it.PinError):
            it.rust_pin(tools, "aarch64")

    def test_the_build_set_has_a_compiler_and_the_c_library(self):
        for arch in ic.DEB_ARCH:
            names = {p["name"] for p in ic.load_toml(ic.lock_path(arch))["build"]["package"]}
            self.assertLessEqual({"gcc", "libc6-dev"}, names)

    def test_no_init_job_installs_rust_with_rustup(self):
        ci = (ic.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        digest = TOOLS["bootstrap"]["image"].split("@", 1)[1]
        for job in ("init-binary", "init-binary-aarch64", "init-rebuild", "init-rebuild-aarch64"):
            body = re.search(rf"\n  {job}:\n(.*?)\n  [a-z]", ci, re.S).group(1)
            self.assertNotIn("rustup", body, job)
            self.assertIn(f"image: debian@{digest}", body, job)
            self.assertIn("scripts/init-build.sh", body, job)


class Fetch(unittest.TestCase):
    def test_a_download_with_the_wrong_hash_is_removed(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ic, "download", fake_download(b"tampered")):
            dest = pathlib.Path(d) / "a.tar.xz"
            with self.assertRaises(it.PinError):
                it.fetch_checked("https://x/a.tar.xz", "0" * 64, dest)
            self.assertFalse(dest.exists())

    def test_a_file_left_in_the_store_is_downloaded_again(self):
        good = b"good"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ic, "download", fake_download(good)):
            dest = pathlib.Path(d) / "a.tar.xz"
            dest.write_bytes(b"planted")
            it.fetch_checked("https://x/a.tar.xz", hashlib.sha256(good).hexdigest(), dest)
            self.assertEqual(dest.read_bytes(), good)

    def test_install_refuses_an_archive_that_is_not_the_pinned_one(self):
        with tempfile.TemporaryDirectory() as d:
            store = pathlib.Path(d)
            pin = it.rust_pin(TOOLS, "x86_64")
            for c in pin["component"]:
                (store / "rust").mkdir(exist_ok=True)
                (store / "rust" / c["url"].rsplit("/", 1)[1]).write_bytes(b"not it")
            with self.assertRaises(it.PinError):
                it.install_toolchain(pin, store, store / "prefix")


def make_crate(path: pathlib.Path, name: str, version: str, extra: dict | None = None) -> str:
    files = {f"{name}-{version}/Cargo.toml": b"[package]\n", f"{name}-{version}/src/lib.rs": b"", **(extra or {})}
    with tarfile.open(path, "w:gz") as tar:
        for n, data in files.items():
            info = tarfile.TarInfo(n)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Crates(unittest.TestCase):
    def test_the_real_lock_has_only_checked_registry_packages(self):
        crates = it.locked_crates((ic.ROOT / "init" / "Cargo.lock").read_text(encoding="utf-8"))
        self.assertIn("rustix", {c["name"] for c in crates})
        self.assertNotIn("ninelives-init", {c["name"] for c in crates})

    def test_a_git_dependency_is_refused(self):
        lock = '[[package]]\nname = "x"\nversion = "1.0.0"\nsource = "git+https://example.com/x"\n'
        with self.assertRaises(it.PinError):
            it.locked_crates(lock)

    def test_a_registry_package_without_a_checksum_is_refused(self):
        lock = f'[[package]]\nname = "x"\nversion = "1.0.0"\nsource = "{it.REGISTRY}"\n'
        with self.assertRaises(it.PinError):
            it.locked_crates(lock)

    def test_a_crate_becomes_a_directory_source_with_the_lock_checksum(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            crate = d / "x-1.0.0.crate"
            sha = make_crate(crate, "x", "1.0.0")
            it.unpack_crate(crate, d / "vendor", "x", "1.0.0", sha)
            self.assertTrue((d / "vendor" / "x-1.0.0" / "src" / "lib.rs").is_file())
            meta = json.loads((d / "vendor" / "x-1.0.0" / ".cargo-checksum.json").read_text())
            self.assertEqual(meta, {"files": {}, "package": sha})

    def test_a_crate_that_escapes_its_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            crate = d / "x-1.0.0.crate"
            make_crate(crate, "x", "1.0.0", {"x-1.0.0/../evil": b"x"})
            with self.assertRaises(it.PinError):
                it.unpack_crate(crate, d / "vendor", "x", "1.0.0", "0" * 64)
            self.assertFalse((d / "evil").exists())

    def test_a_crate_that_does_not_match_the_lock_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            lock = f'[[package]]\nname = "x"\nversion = "1.0.0"\nsource = "{it.REGISTRY}"\nchecksum = "{"1" * 64}"\n'
            with mock.patch.object(ic, "download", fake_download(b"other bytes")):
                with self.assertRaises(it.PinError):
                    it.fetch_crates(lock, d)
            self.assertFalse((d / "vendor").exists())


class Bump(unittest.TestCase):
    def gpgv_result(self, stdout: str, rc: int = 0):
        return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")

    def validsig(self, fpr: str) -> str:
        return f"[GNUPG:] VALIDSIG {'A' * 40} 2026-01-01 1 0 4 0 1 10 00 {fpr}\n"

    def test_the_pinned_release_key_is_in_the_fingerprint_list(self):
        fprs, errors = ic.load_fingerprints(pt.KEYS / "fingerprints")
        self.assertEqual(errors, [])
        self.assertEqual(fprs, {"108F66205EAEB0AAA8DD5E1C85AB96E6FA1BE5FE"})
        self.assertTrue(ic.dearmor((pt.KEYS / "release-key.asc").read_text(encoding="ascii")))

    def test_a_signature_by_a_pinned_key_passes(self):
        fprs = {"B" * 40}
        with mock.patch.object(subprocess, "run", return_value=self.gpgv_result(self.validsig("B" * 40))):
            pt.verify_signature(pathlib.Path("a"), pathlib.Path("a.asc"), fprs)

    def test_a_signature_by_another_key_fails(self):
        with mock.patch.object(subprocess, "run", return_value=self.gpgv_result(self.validsig("C" * 40))):
            with self.assertRaises(it.PinError):
                pt.verify_signature(pathlib.Path("a"), pathlib.Path("a.asc"), {"B" * 40})

    def test_a_bad_signature_fails(self):
        out = "[GNUPG:] BADSIG 1234 someone\n"
        with mock.patch.object(subprocess, "run", return_value=self.gpgv_result(out, 1)):
            with self.assertRaises(it.PinError):
                pt.verify_signature(pathlib.Path("a"), pathlib.Path("a.asc"), {"B" * 40})

    def test_a_published_hash_that_disagrees_with_the_archive_fails(self):
        def download(url, dest, attempts=5):
            dest.write_bytes(b"x" * 64 + b"  a\n" if url.endswith(".sha256") else b"archive")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ic, "download", download):
            with self.assertRaises(it.PinError):
                pt.pin_component("rustc", "1.99.1", it.TRIPLE["x86_64"], pathlib.Path(d), {"B" * 40})

    def test_a_component_is_pinned_only_after_hash_and_signature(self):
        archive = b"archive"
        sha = hashlib.sha256(archive).hexdigest()

        def download(url, dest, attempts=5):
            dest.write_bytes(f"{sha}  a.tar.xz\n".encode() if url.endswith(".sha256") else archive)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ic, "download", download), \
                mock.patch.object(pt, "verify_signature") as verify:
            pin = pt.pin_component("cargo", "1.99.1", it.TRIPLE["aarch64"], pathlib.Path(d), {"B" * 40})
        verify.assert_called_once()
        self.assertEqual(pin["sha256"], sha)
        self.assertEqual(pin["url"], f"{it.DIST}/cargo-1.99.1-aarch64-unknown-linux-gnu.tar.xz")

    def test_the_rendered_block_reads_back_as_the_current_pins(self):
        pins = {arch: it.rust_pin(TOOLS, arch)["component"] for arch in ic.DEB_ARCH}
        block = pt.render(TOOLS["rust"]["version"], pins)
        text = (ic.IMAGES / "tools.toml").read_text(encoding="utf-8")
        self.assertIn(block, text)
        self.assertEqual(pt.replace_block(text, block), text)


if __name__ == "__main__":
    unittest.main()
