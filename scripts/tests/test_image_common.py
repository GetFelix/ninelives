"""Unit tests for scripts/image_common.py and scripts/image_lock.py."""
import io
import pathlib
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import image_common as ic  # noqa: E402
import image_lock  # noqa: E402


class Versions(unittest.TestCase):
    def test_dpkg_order(self):
        order = ["1.0~rc1", "1.0", "1.0-1", "1.0-1+b1", "1.0+deb13u1", "1:0.5", "2:0.1"]
        for lo, hi in zip(order, order[1:]):
            self.assertEqual(ic.vercmp(lo, hi), -1, (lo, hi))
            self.assertEqual(ic.vercmp(hi, lo), 1, (lo, hi))
        self.assertEqual(ic.vercmp("2.41-12+deb13u4", "2.41-12+deb13u4"), 0)
        self.assertEqual(ic.vercmp("1.9", "1.10"), -1)

    def test_dependency_names(self):
        self.assertEqual(ic.dep_alternatives("libc6 (>= 2.34), a:any | b, "), [["libc6"], ["a", "b"]])


class Deb822(unittest.TestCase):
    def test_paragraphs_and_continuations(self):
        paras = ic.parse_deb822("Package: a\nDepends: x,\n y\n\nPackage: b\n")
        self.assertEqual(paras[0]["Depends"], "x,\ny")
        self.assertEqual(paras[1]["Package"], "b")

    def test_higher_version_wins(self):
        pk = ic.parse_packages("Package: a\nVersion: 1.0\n\nPackage: a\nVersion: 1.0+deb13u1\n\nPackage: b\nVersion: 2\n")
        self.assertEqual(pk["a"]["Version"], "1.0+deb13u1")

    def test_release_hashes(self):
        text = "Origin: x\nSHA256:\n " + "a" * 64 + " 10 main/binary-amd64/Packages.xz\n"
        self.assertEqual(ic.release_hashes(text), {"main/binary-amd64/Packages.xz": ("a" * 64, 10)})


def make_deb(members) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "link":
                info.type, info.linkname = tarfile.SYMTYPE, payload
                tar.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    data = buf.getvalue()

    def entry(name, body):
        return name.ljust(16).encode() + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6) + b"100644".ljust(8) + \
            str(len(body)).ljust(10).encode() + b"`\n" + body + (b"\n" if len(body) & 1 else b"")

    return b"!<arch>\n" + entry("debian-binary", b"2.0\n") + entry("data.tar.xz", data)


class Debs(unittest.TestCase):
    def test_members(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "x.deb"
            p.write_bytes(make_deb([("./usr/", "dir", None), ("./usr/.hidden", "file", b"hi"), ("./bin", "link", "usr/bin")]))
            got = {m.path: m for m in ic.deb_members(p)}
            self.assertEqual(got["/usr"].kind, "dir")
            self.assertEqual(got["/usr/.hidden"].sha256, "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4")
            self.assertEqual(got["/bin"].link, "usr/bin")

    def test_special_files_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "x.deb"
            p.write_bytes(make_deb([("./f", "fifo", None)]))
            with self.assertRaises(ValueError):
                ic.deb_members(p)


def elf(flags_stack=6, load_flags=5, needed=("libc.so.6",)) -> bytes:
    """A tiny ELF64 with PT_LOAD, PT_DYNAMIC and PT_GNU_STACK."""
    strtab = b"\0" + b"".join(n.encode() + b"\0" for n in needed)
    offs, o = [], 1
    for n in needed:
        offs.append(o)
        o += len(n) + 1
    dyn = b"".join(struct.pack("<qQ", 1, off) for off in offs) + struct.pack("<qQ", 5, 0x1000 + 0x200) + struct.pack("<qQ", 0, 0)
    phoff, phnum = 64, 3
    dyn_off = 64 + 56 * phnum
    str_off = dyn_off + len(dyn)
    total = str_off + len(strtab)
    hdr = bytearray(64)
    hdr[:4] = b"\x7fELF"
    hdr[4], hdr[5] = 2, 1
    struct.pack_into("<HH", hdr, 16, 3, 62)
    struct.pack_into("<Q", hdr, 32, phoff)
    struct.pack_into("<HH", hdr, 54, 56, phnum)
    # The string table sits at virtual address 0x1200 inside the one PT_LOAD, which maps file offset 0 to 0x1000.
    ph = struct.pack("<IIQQQQQQ", 1, load_flags, 0, 0x1000, 0x1000, 0x1000, 0x1000, 0x1000)
    ph += struct.pack("<IIQQQQQQ", 2, 6, dyn_off, 0x1000 + dyn_off, 0, len(dyn), len(dyn), 8)
    ph += struct.pack("<IIQQQQQQ", 0x6474E551, flags_stack, 0, 0, 0, 0, 0, 16)
    body = bytes(hdr) + ph + dyn
    body += b"\0" * (0x200 - len(body)) if len(body) < 0x200 else b""
    return body + strtab


class Elfs(unittest.TestCase):
    def test_needed_and_flags(self):
        e = ic.Elf(elf())
        self.assertEqual(e.needed, ["libc.so.6"])
        self.assertFalse(e.executable_stack)
        self.assertFalse(e.writable_executable)

    def test_executable_stack(self):
        self.assertTrue(ic.Elf(elf(flags_stack=7)).executable_stack)

    def test_writable_executable_segment(self):
        self.assertTrue(ic.Elf(elf(load_flags=7)).writable_executable)

    def test_not_elf(self):
        with self.assertRaises(ValueError):
            ic.Elf(b"#!/bin/sh\n")


class Lock(unittest.TestCase):
    def test_repository_lock_is_consistent(self):
        self.assertEqual(image_lock.check(), [])

    def universe(self):
        def st(name, deps="", provides="", essential=None):
            d = {"Package": name, "Version": "1", "Depends": deps, "Provides": provides}
            if essential:
                d["Essential"] = "yes"
            return d
        return {"a": st("a", "b | c, libc6 (>= 1)"), "b": st("b"), "c": st("c"), "libc6": st("libc6"),
                "awk-impl": st("awk-impl", provides="awk"), "u": st("u", "awk"), "e": st("e", essential=True)}

    def test_closure_takes_first_alternative_and_providers(self):
        self.assertEqual(image_lock.closure(self.universe(), ["a", "u"], False), ["a", "awk-impl", "b", "libc6", "u"])

    def test_essential_packages_are_roots(self):
        self.assertIn("e", image_lock.closure(self.universe(), ["b"], True))

    def test_unknown_root_fails(self):
        with self.assertRaises(SystemExit):
            image_lock.closure(self.universe(), ["nope"], False)

    def test_unresolvable_dependency_fails(self):
        u = self.universe()
        u["x"] = {"Package": "x", "Version": "1", "Depends": "ghost"}
        with self.assertRaises(SystemExit):
            image_lock.closure(u, ["x"], False)


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "needs gpg and gpgv")
class InRelease(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.gnupg = self.d / "gnupg"
        self.gnupg.mkdir(mode=0o700)
        self.keys = self.d / "keys"
        self.keys.mkdir()

    def key(self, name):
        subprocess.run(["gpg", "--homedir", str(self.gnupg), "--batch", "--passphrase", "", "--quick-gen-key", name, "ed25519", "sign", "never"],
                       check=True, capture_output=True)
        fpr = subprocess.run(["gpg", "--homedir", str(self.gnupg), "--with-colons", "--list-keys", name], check=True, capture_output=True,
                             text=True).stdout
        fpr = next(ln.split(":")[9] for ln in fpr.splitlines() if ln.startswith("fpr"))
        pub = subprocess.run(["gpg", "--homedir", str(self.gnupg), "--armor", "--export", fpr], check=True, capture_output=True, text=True).stdout
        return fpr, pub

    def sign(self, fpr, text):
        src = self.d / "Release"
        src.write_text(text)
        out = self.d / "InRelease"
        subprocess.run(["gpg", "--homedir", str(self.gnupg), "--batch", "--yes", "--local-user", fpr, "--clearsign", "-o", str(out), str(src)],
                       check=True, capture_output=True)
        return out

    def test_pinned_signature_passes_and_text_is_the_signed_text(self):
        fpr, pub = self.key("a")
        (self.keys / "a.asc").write_text(pub)
        out = self.sign(fpr, "Suite: x\n")
        self.assertEqual(ic.verify_inrelease(out, self.keys, {fpr}).strip(), "Suite: x")

    def test_unpinned_signer_fails(self):
        fpr, pub = self.key("a")
        fpr2, pub2 = self.key("b")
        (self.keys / "a.asc").write_text(pub)
        (self.keys / "b.asc").write_text(pub2)
        out = self.sign(fpr2, "Suite: x\n")
        with self.assertRaises(ValueError):
            ic.verify_inrelease(out, self.keys, {fpr})

    def test_altered_text_fails(self):
        fpr, pub = self.key("a")
        (self.keys / "a.asc").write_text(pub)
        out = self.sign(fpr, "Suite: x\n")
        out.write_text(out.read_text().replace("Suite: x", "Suite: y"))
        with self.assertRaises(ValueError):
            ic.verify_inrelease(out, self.keys, {fpr})

    def test_repository_keys_dearmor_and_match_fingerprints(self):
        keys = ic.IMAGES / "keys" / "debian"
        fprs, errors = ic.load_fingerprints(keys / "fingerprints")
        self.assertEqual(errors, [])
        found = set()
        for p in keys.glob("*.asc"):
            ic.dearmor(p.read_text())
            out = subprocess.run(["gpg", "--show-keys", "--with-colons", str(p)], capture_output=True, text=True).stdout
            found.add(next(ln.split(":")[9] for ln in out.splitlines() if ln.startswith("fpr")))
        self.assertEqual(found, fprs)


if __name__ == "__main__":
    unittest.main()
