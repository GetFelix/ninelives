"""Unit tests for scripts/fetch_kernel.py.

Run from the repository root: python3 -m unittest discover -s scripts/tests
The signature tests make a throwaway key and skip when gpg, gpgv or xz is
missing.
"""
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import fetch_kernel as fk  # noqa: E402


class Pins(unittest.TestCase):
    def pin(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "VERSION"
            path.write_text(text)
            return fk.load_pin(path)[1]

    def test_repository_pin_is_well_formed(self):
        self.assertEqual(fk.load_pin(fk.KERNEL / "VERSION")[1], [])
        fprs, errors = fk.load_fingerprints(fk.KERNEL / "keys" / "fingerprints")
        self.assertEqual(errors, [])
        self.assertTrue(fprs)

    def test_repository_keys_dearmor(self):
        for p in (fk.KERNEL / "keys").glob("*.asc"):
            self.assertTrue(fk.dearmor(p.read_text()))

    def test_url_must_be_the_versions_own(self):
        errors = self.pin(
            'version = "6.18.64"\n'
            'url = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.63.tar.xz"\n'
            'signature = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.64.tar.sign"\n'
            f'sha256 = "{"0" * 64}"\n')
        self.assertEqual(errors, ["VERSION: url must be https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.64.tar.xz"])

    def test_inexact_version_fails(self):
        self.assertTrue(self.pin('version = "6.18"\n'))

    def test_bad_hash_fails(self):
        errors = self.pin(
            'version = "6.18.64"\n'
            'url = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.64.tar.xz"\n'
            'signature = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.64.tar.sign"\n'
            'sha256 = "ABC"\n')
        self.assertEqual(errors, ["VERSION: sha256 is not 64 lowercase hex digits"])

    def test_short_fingerprint_fails(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "fingerprints"
            path.write_text("# c\n38DBBDC86092693E Greg\n")
            self.assertTrue(fk.load_fingerprints(path)[1])

    def test_armor_checksum_is_checked(self):
        text = next((fk.KERNEL / "keys").glob("*.asc")).read_text().splitlines()
        crc = next(i for i, line in enumerate(text) if line.startswith("="))
        text[crc] = "=AAAA"
        with self.assertRaises(ValueError):
            fk.dearmor("\n".join(text))


    def test_other_series_fails(self):
        errors = self.pin(
            'version = "6.12.58"\n'
            'url = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.58.tar.xz"\n'
            'signature = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.58.tar.sign"\n'
            f'sha256 = "{"0" * 64}"\n')
        self.assertEqual(errors, ["VERSION: 6.12.58 is not in the pinned 6.18.y series"])


def pin_text(version: str) -> str:
    base = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{version}"
    return f'version = "{version}"\nurl = "{base}.tar.xz"\nsignature = "{base}.tar.sign"\nsha256 = "{"0" * 64}"\n'


class AgainstBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.base, self.head = root / "base", root / "head"
        for d in (self.base, self.head):
            (d / "keys").mkdir(parents=True)
            (d / "VERSION").write_text(pin_text("6.18.64"))
            (d / "keys" / "fingerprints").write_text("A" * 40 + "\n")

    def test_unchanged_passes(self):
        self.assertEqual(fk.check_against_base(self.head, self.base), [])

    def test_bump_alone_passes(self):
        (self.head / "VERSION").write_text(pin_text("6.18.65"))
        self.assertEqual(fk.check_against_base(self.head, self.base), [])

    def test_key_change_alone_passes(self):
        (self.head / "keys" / "new.asc").write_text("key")
        self.assertEqual(fk.check_against_base(self.head, self.base), [])

    def test_downgrade_fails(self):
        (self.head / "VERSION").write_text(pin_text("6.18.63"))
        self.assertEqual(fk.check_against_base(self.head, self.base),
                         ["VERSION: 6.18.63 is older than 6.18.64 on the target branch"])

    def test_bump_with_a_key_change_fails(self):
        for change in ("add", "edit", "remove"):
            with self.subTest(change=change):
                self.setUp()
                (self.head / "VERSION").write_text(pin_text("6.18.65"))
                fprs = self.head / "keys" / "fingerprints"
                if change == "add":
                    (self.head / "keys" / "new.asc").write_text("key")
                elif change == "edit":
                    fprs.write_text("B" * 40 + "\n")
                else:
                    fprs.unlink()
                self.assertEqual(fk.check_against_base(self.head, self.base),
                                 ["kernel/keys/ and kernel/VERSION both change; "
                                  "change the keys in their own pull request"])

    def test_bump_refuses_a_downgrade_or_another_series(self):
        # Refused before anything is downloaded.
        for version, message in (("6.18.63", "is not newer than"), ("6.19.1", "is not in the pinned 6.18.y")):
            with self.subTest(version=version):
                r = subprocess.run([sys.executable, "-I", str(SCRIPTS / "fetch_kernel.py"),
                                    "--out", self.tmp.name, "--bump", version], capture_output=True, text=True)
                self.assertEqual(r.returncode, 2)
                self.assertIn(message, r.stderr)

    def test_base_without_a_pin_passes(self):
        (self.base / "VERSION").unlink()
        self.assertEqual(fk.check_against_base(self.head, self.base), [])


class Status(unittest.TestCase):
    def test_validsig_primary_is_the_last_field(self):
        status = (
            "[GNUPG:] GOODSIG 38DBBDC86092693E Greg\n"
            "[GNUPG:] VALIDSIG SUBKEYFPR 2026-11-17 1794910415 0 4 0 1 10 00 "
            "647F28654894E3BD457199BE38DBBDC86092693E\n")
        self.assertEqual(fk.valid_signers(status), ({"647F28654894E3BD457199BE38DBBDC86092693E"}, []))

    def test_bad_status_is_reported(self):
        self.assertEqual(fk.valid_signers("[GNUPG:] BADSIG 38DB Greg\n")[1], ["[GNUPG:] BADSIG 38DB Greg"])


@unittest.skipUnless(all(shutil.which(t) for t in ("gpg", "gpgv", "xz")), "needs gpg, gpgv and xz")
class Verify(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.home = self.dir / "gnupg"
        self.home.mkdir(mode=0o700)
        self.addCleanup(subprocess.run, ["gpgconf", "--homedir", str(self.home), "--kill", "gpg-agent"],
                        capture_output=True)
        self.keys = self.dir / "keys"
        self.keys.mkdir()
        self.signer = self.make_key("signer@example.invalid", "signer.asc")
        self.other = self.make_key("other@example.invalid", None)
        self.tar = self.dir / "linux-1.2.3.tar"
        self.tar.write_bytes(b"pretend this is a kernel tree\n" * 100)
        subprocess.run(["xz", "-k", str(self.tar)], check=True)
        self.tarxz = self.dir / "linux-1.2.3.tar.xz"
        self.pin = {"sha256": hashlib.sha256(self.tarxz.read_bytes()).hexdigest()}

    def gpg(self, *args: str, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(["gpg", "--batch", "--homedir", str(self.home), *args],
                              check=True, capture_output=True, **kw)

    def make_key(self, uid: str, export: str | None) -> str:
        self.gpg("--passphrase", "", "--quick-gen-key", uid, "ed25519", "sign", "never")
        out = self.gpg("--with-colons", "--list-keys", uid, text=True).stdout
        fpr = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
        if export:
            (self.keys / export).write_bytes(self.gpg("--armor", "--export", fpr).stdout)
        return fpr

    def sign(self, fpr: str) -> pathlib.Path:
        sig = self.dir / f"{fpr}.sign"
        self.gpg("--local-user", fpr, "--armor", "--detach-sign", "--output", str(sig), str(self.tar))
        return sig

    def test_good_signature_passes(self):
        self.assertEqual(fk.verify(self.pin, self.tarxz, self.sign(self.signer), self.keys, {self.signer}), [])

    def test_hash_mismatch_fails_before_the_signature(self):
        errors = fk.verify({"sha256": "0" * 64}, self.tarxz, self.dir / "missing.sign", self.keys, {self.signer})
        self.assertEqual(len(errors), 1)
        self.assertIn("is not the pinned", errors[0])

    def test_key_in_keyring_but_not_pinned_fails(self):
        errors = fk.verify(self.pin, self.tarxz, self.sign(self.signer), self.keys, {"0" * 40})
        self.assertIn("none of them in kernel/keys/fingerprints", errors[0])

    def test_signer_not_in_keyring_fails(self):
        errors = fk.verify(self.pin, self.tarxz, self.sign(self.other), self.keys, {self.signer, self.other})
        self.assertIn("gpgv failed", errors[0])

    def test_signature_over_other_content_fails(self):
        sig = self.sign(self.signer)
        self.tar.write_bytes(b"something else\n")
        os.remove(self.tarxz)
        subprocess.run(["xz", "-k", str(self.tar)], check=True)
        pin = {"sha256": hashlib.sha256(self.tarxz.read_bytes()).hexdigest()}
        errors = fk.verify(pin, self.tarxz, sig, self.keys, {self.signer})
        self.assertIn("gpgv failed", errors[0])


if __name__ == "__main__":
    unittest.main()
