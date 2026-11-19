"""Unit tests for scripts/image_stage.py and the fetch phase's pin checks."""
import hashlib
import io
import os
import pathlib
import sys
import tarfile
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import fetch_image  # noqa: E402
import image_common as ic  # noqa: E402
import image_stage as st  # noqa: E402


def archive(path: pathlib.Path, members) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            if kind == "link":
                info.type, info.linkname = tarfile.SYMTYPE, payload
                tar.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))


class Unpack(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.root = self.d / "root"
        self.root.mkdir()

    def test_takes_only_the_named_member_with_mode_0555(self):
        a = self.d / "a.tar.gz"
        archive(a, [("pkg/", "dir", None), ("pkg/felixd", "file", b"bin"), ("pkg/README.md", "file", b"doc")])
        got = st.safe_unpack(a, {"felixd": "/usr/lib/felixd/felixd"}, self.root)
        self.assertEqual(got, {"/usr/lib/felixd/felixd": hashlib.sha256(b"bin").hexdigest()})
        self.assertEqual(sorted(p.name for p in (self.root / "usr/lib/felixd").iterdir()), ["felixd"])
        self.assertEqual((self.root / "usr/lib/felixd/felixd").stat().st_mode & 0o7777, 0o555)

    def test_refuses_a_link(self):
        a = self.d / "a.tar.gz"
        archive(a, [("pkg/felixd", "file", b"x"), ("pkg/evil", "link", "/etc/passwd")])
        with self.assertRaises(SystemExit):
            st.safe_unpack(a, {"felixd": "/usr/bin/felixd"}, self.root)

    def test_refuses_absolute_and_dotdot_paths(self):
        for name in ("/etc/x", "pkg/../../x"):
            a = self.d / "a.tar.gz"
            archive(a, [(name, "file", b"x"), ("pkg/felixd", "file", b"x")])
            with self.assertRaises(SystemExit):
                st.safe_unpack(a, {"felixd": "/usr/bin/felixd"}, self.root)

    def test_missing_or_ambiguous_member(self):
        a = self.d / "a.tar.gz"
        archive(a, [("pkg/other", "file", b"x")])
        with self.assertRaises(SystemExit):
            st.safe_unpack(a, {"felixd": "/usr/bin/felixd"}, self.root)
        archive(a, [("a/felixd", "file", b"x"), ("b/felixd", "file", b"y")])
        with self.assertRaises(SystemExit):
            st.safe_unpack(a, {"felixd": "/usr/bin/felixd"}, self.root)


class RemovePackages(unittest.TestCase):
    def test_removes_only_unlisted_packages_files(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "var/lib/dpkg/info").mkdir(parents=True)
        (d / "var/lib/dpkg/status").write_text(
            "Package: keep\nStatus: install ok installed\nVersion: 1\n\nPackage: drop\nStatus: install ok installed\nVersion: 2\n")
        for p in ("usr/bin", "usr/lib/keep", "usr/lib/drop"):
            (d / p).mkdir(parents=True, exist_ok=True)
        (d / "usr/lib/keep/a").write_text("a")
        (d / "usr/lib/drop/b").write_text("b")
        (d / "usr/bin/sh").symlink_to("dash")
        (d / "var/lib/dpkg/info/keep.list").write_text("/.\n/usr\n/usr/lib\n/usr/lib/keep\n/usr/lib/keep/a\n")
        (d / "var/lib/dpkg/info/drop.list").write_text("/.\n/usr\n/usr/lib\n/usr/lib/drop\n/usr/lib/drop/b\n/usr/bin/sh\n")
        self.assertEqual(st.remove_packages(d, {"keep"}), ["drop"])
        self.assertTrue((d / "usr/lib/keep/a").exists())
        self.assertFalse((d / "usr/lib/drop").exists())
        self.assertFalse((d / "usr/bin/sh").is_symlink())
        self.assertTrue((d / "usr/lib").exists())


class Generated(unittest.TestCase):
    def test_passwd_and_group_come_from_users_toml(self):
        passwd, group = st.passwd_group(ic.load_toml(ic.IMAGES / "users.toml"))
        self.assertEqual(passwd, "root:x:0:0:root:/nonexistent:/usr/sbin/nologin\n")
        self.assertEqual(group, "root:x:0:\n")

    def test_os_release_names_the_variant(self):
        text = st.os_release("production", "a" * 40)
        self.assertIn("VARIANT_ID=production", text)
        self.assertIn("BUILD_ID=" + "a" * 12, text)

    @unittest.skipIf(os.geteuid() != 0, "needs root to chown")
    def test_normalise_strips_write_bits_and_sets_time(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "f").write_text("x")
        os.chmod(d / "f", 0o4777)
        st.normalise(d, 1234)
        self.assertEqual((d / "f").stat().st_mode & 0o7777, 0o4555)
        self.assertEqual(int((d / "f").stat().st_mtime), 1234)


class FetchPins(unittest.TestCase):
    def test_inrelease_that_is_not_the_pinned_one_fails(self):
        out = pathlib.Path(tempfile.mkdtemp())
        inrel = fetch_image.mirror_path(out, "20261118T000000Z", "debian", "dists/trixie/InRelease")
        inrel.parent.mkdir(parents=True)
        inrel.write_text("a replayed, validly signed older file")
        lock = {"root": {"package": [], "suite": [{"archive": "debian", "name": "trixie", "inrelease_sha256": "0" * 64}]}}
        with self.assertRaises(SystemExit) as cm:
            fetch_image.fetch_release(out, lock, "root", "amd64", "20261118T000000Z")
        self.assertIn("lock pins", str(cm.exception))

    def test_wrong_hash_fails_and_removes_the_file(self):
        d = pathlib.Path(tempfile.mkdtemp())
        f = d / "x.tar.gz"
        f.write_bytes(b"not what was pinned")
        with self.assertRaises(SystemExit):
            fetch_image.fetch_pinned("https://example.invalid/x", "0" * 64, f)
        self.assertFalse(f.exists())

    def test_matching_hash_passes_without_a_download(self):
        d = pathlib.Path(tempfile.mkdtemp())
        f = d / "x"
        f.write_bytes(b"ok")
        fetch_image.fetch_pinned("https://example.invalid/x", hashlib.sha256(b"ok").hexdigest(), f)

    def test_downloads_only_from_https_or_the_snapshot(self):
        with self.assertRaises(ValueError):
            ic.download("http://evil.example/x", pathlib.Path(tempfile.mkdtemp()) / "x")
        with self.assertRaises(ValueError):
            ic.download("file:///etc/passwd", pathlib.Path(tempfile.mkdtemp()) / "x")


if __name__ == "__main__":
    unittest.main()
