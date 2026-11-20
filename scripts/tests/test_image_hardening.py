"""Unit tests for scripts/image_hardening.py.

Run from the repository root: python3 -m unittest discover -s scripts/tests
"""
import hashlib
import io
import pathlib
import re
import sys
import tarfile
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import image_common as ic  # noqa: E402
import image_hardening as ih  # noqa: E402


def make_tarball(path: pathlib.Path) -> str:
    with tarfile.open(path, "w:gz") as t:
        data = b"#!/usr/bin/env python3\n"
        info = tarfile.TarInfo("khc-abc/bin/kernel-hardening-checker")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UnpackChecker(unittest.TestCase):
    def fetch(self, pinned_sha):
        tmp = pathlib.Path(tempfile.mkdtemp())
        src = tmp / "src.tar.gz"
        real = make_tarball(src)
        work = tmp / "work"
        work.mkdir()

        pin = {"sha256": pinned_sha or real}
        return ih.unpack_checker(src, pin, work)

    def test_matching_hash_returns_the_script(self):
        self.assertEqual(self.fetch(None).name, "kernel-hardening-checker")

    def test_wrong_hash_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.fetch("0" * 64)
        self.assertIn("tools.toml pins", str(cm.exception))


class Modules(unittest.TestCase):
    def test_modules_off_passes(self):
        self.assertEqual(ih.module_errors("# CONFIG_MODULES is not set\n"), [])

    def test_modules_on_fails(self):
        self.assertEqual(len(ih.module_errors("CONFIG_MODULES=y\n")), 1)

    def test_modules_missing_fails(self):
        self.assertEqual(len(ih.module_errors("CONFIG_X=y\n")), 1)


class Pins(unittest.TestCase):
    def test_ci_and_tools_toml_name_the_same_commit(self):
        pin = ic.load_toml(ic.IMAGES / "tools.toml")["kernel_hardening_checker"]
        ci = (ic.ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertEqual(re.search(r"KHC_COMMIT: (\w+)", ci).group(1), pin["commit"])
        self.assertIn(pin["commit"], pin["url"])


if __name__ == "__main__":
    unittest.main()
