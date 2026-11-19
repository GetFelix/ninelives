"""Unit tests for scripts/sigstore_verify.py and the signed path of fetch_image.py.

Run from the repository root: python3 -m unittest discover -s scripts/tests

The fixtures are felixd v0.1.0's real SHA256SUMS and its Sigstore bundle
(scripts/tests/fixtures/sigstore/), downloaded from the release. The bundle
tests need the pinned cosign: set NL_COSIGN to its path (CI downloads it and
checks the hash in images/tools.toml); they skip without it.
"""
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_components as cc  # noqa: E402
import fetch_image as fi  # noqa: E402
import image_common as ic  # noqa: E402
import sigstore_verify as sv  # noqa: E402

FIXTURES = SCRIPTS / "tests" / "fixtures" / "sigstore"
ROOT = ic.IMAGES / "keys" / "sigstore" / "trusted_root.json"
IDENTITY = "https://github.com/GetFelix/felixd/.github/workflows/release.yml@refs/tags/v0.1.0"
ISSUER = "https://token.actions.githubusercontent.com"
ARTIFACT = "felixd-v0.1.0-x86_64-unknown-linux-gnu.tar.gz"
ARTIFACT_SHA = "1204fd542c54525e5b5ef4a553ed49dc769e3063b38f00874a7a5ce2aab246b8"
COSIGN = os.environ.get("NL_COSIGN") or shutil.which("cosign")


class Pins(unittest.TestCase):
    def test_trusted_root_matches_its_pin(self):
        tools = ic.load_toml(ic.IMAGES / "tools.toml")
        self.assertEqual(ic.sha256_file(ROOT), tools["sigstore"]["trusted_root_sha256"])
        self.assertRegex(tools["cosign"]["sha256"], r"[0-9a-f]{64}")
        self.assertIn(tools["cosign"]["version"], tools["cosign"]["url"])


class Sums(unittest.TestCase):
    def test_parse(self):
        sums = sv.parse_sums((FIXTURES / "SHA256SUMS").read_text())
        self.assertEqual(sums[ARTIFACT], ARTIFACT_SHA)
        self.assertEqual(len(sums), 3)

    def test_malformed_and_duplicate_lines_are_refused(self):
        with self.assertRaises(sv.VerifyError):
            sv.parse_sums("abc  file\n")
        line = f"{'a' * 64}  f\n"
        with self.assertRaises(sv.VerifyError):
            sv.parse_sums(line + line)

    def test_artifact_must_be_listed(self):
        sums = sv.parse_sums((FIXTURES / "SHA256SUMS").read_text())
        with self.assertRaisesRegex(sv.VerifyError, "does not list"):
            sv.check_artifact(sums, "felixd-v0.1.0-riscv.tar.gz", ARTIFACT_SHA, ARTIFACT_SHA)

    def test_pin_must_equal_the_signed_line(self):
        sums = sv.parse_sums((FIXTURES / "SHA256SUMS").read_text())
        other = "0" * 64
        with self.assertRaisesRegex(sv.VerifyError, "pins"):
            sv.check_artifact(sums, ARTIFACT, other, ARTIFACT_SHA)

    def test_download_must_equal_the_signed_line(self):
        sums = sv.parse_sums((FIXTURES / "SHA256SUMS").read_text())
        with self.assertRaisesRegex(sv.VerifyError, "downloaded"):
            sv.check_artifact(sums, ARTIFACT, ARTIFACT_SHA, "0" * 64)
        sv.check_artifact(sums, ARTIFACT, ARTIFACT_SHA, ARTIFACT_SHA)


@unittest.skipUnless(COSIGN, "cosign not available (set NL_COSIGN)")
class Bundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.sums = self.dir / "SHA256SUMS"
        self.bundle = self.dir / "SHA256SUMS.sigstore.json"
        shutil.copy(FIXTURES / "SHA256SUMS", self.sums)
        shutil.copy(FIXTURES / "SHA256SUMS.sigstore.json", self.bundle)

    def verify(self, identity=IDENTITY, issuer=ISSUER, root=ROOT):
        sv.verify_bundle(pathlib.Path(COSIGN), root, self.bundle, self.sums, identity, issuer)

    def test_good_bundle(self):
        self.verify()

    def test_wrong_identity(self):
        with self.assertRaisesRegex(sv.VerifyError, "does not verify"):
            self.verify(identity=IDENTITY.replace("v0.1.0", "v0.1.1"))

    def test_identity_is_not_a_prefix_match(self):
        with self.assertRaises(sv.VerifyError):
            self.verify(identity="https://github.com/GetFelix/felixd")

    def test_wrong_issuer(self):
        with self.assertRaisesRegex(sv.VerifyError, "does not verify"):
            self.verify(issuer="https://accounts.google.com")

    def test_tampered_sums(self):
        text = self.sums.read_text().replace(ARTIFACT_SHA, "0" * 64)
        self.sums.write_text(text)
        with self.assertRaisesRegex(sv.VerifyError, "does not verify"):
            self.verify()

    def test_wrong_trusted_root(self):
        bad = self.dir / "root.json"
        bad.write_text('{"mediaType": "application/vnd.dev.sigstore.trustedroot+json;version=0.1"}')
        with self.assertRaises(sv.VerifyError):
            self.verify(root=bad)

    def test_fetch_component_accepts_the_signed_artifact(self):
        self.fetch(ARTIFACT_SHA)

    def test_fetch_component_refuses_a_pin_the_signer_never_signed(self):
        with self.assertRaisesRegex(SystemExit, "pins"):
            self.fetch("f" * 64)

    def test_fetch_component_refuses_an_unlisted_artifact(self):
        with self.assertRaisesRegex(SystemExit, "does not list"):
            self.fetch(ARTIFACT_SHA, name="felixd-v0.1.0-riscv64-unknown-linux-gnu.tar.gz")

    def fetch(self, pinned, name=ARTIFACT):
        base = "https://github.com/GetFelix/felixd/releases/download/v0.1.0/"
        c = {
            "name": "felixd", "version": "0.1.0",
            "signature": {"kind": "sigstore", "checksums": base + "SHA256SUMS",
                          "bundle": base + "SHA256SUMS.sigstore.json", "identity": IDENTITY, "issuer": ISSUER},
            "artifacts": {"x86_64": {"url": base + name, "sha256": pinned}},
        }

        def download(url, dest, attempts=5):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((FIXTURES / url.rsplit("/", 1)[1]).read_bytes() if url.endswith(("SUMS", ".json")) else b"x")

        # The artifact itself is 12 MB, so its hash is stood in for here.
        real = ic.sha256_file
        with mock.patch.object(ic, "download", download), \
                mock.patch.object(ic, "sha256_file", lambda p: ARTIFACT_SHA if p.name == name else real(p)), \
                mock.patch.object(fi, "fetch_pinned", lambda url, sha, dest: download(url, dest)):
            return fi.fetch_component(c, "x86_64", self.dir / "out", (pathlib.Path(COSIGN), ROOT))


class Manifest(unittest.TestCase):
    def check(self, mutate):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "components.toml"
            text = (ic.IMAGES / "components.toml").read_text()
            path.write_text(mutate(text))
            return cc.check(path)

    def test_repository_manifest_is_valid(self):
        self.assertEqual(self.check(lambda t: t), [])

    def test_sigstore_needs_every_field(self):
        for key in ("checksums", "bundle", "identity", "issuer"):
            errors = self.check(lambda t: "\n".join(l for l in t.splitlines() if not l.startswith(f"{key} = ")))
            self.assertTrue(any(f"needs signature.{key}" in e for e in errors), (key, errors))

    def test_empty_identity_is_refused(self):
        errors = self.check(lambda t: t.replace('identity = "https://github.com/GetFelix/felixd/.github/workflows/release.yml@refs/tags/v0.1.0"', 'identity = ""'))
        self.assertTrue(any("needs signature.identity" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
