"""Unit tests for scripts/bootstrap.sh.

Run from the repository root: python3 -m unittest discover -s scripts/tests
The script's download, dpkg and dpkg-query are replaced by stubs, so nothing
here touches the network or the machine's packages. Skips without bash 4.4+.
"""
import hashlib
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bootstrap.sh"
BASH = shutil.which("bash")

GET = """#!/bin/sh
# URL DEST: serve from $FIXTURE_MIRROR by the file name
cp "$FIXTURE_MIRROR/$(basename "$1")" "$2"
echo "$1" >> "$FIXTURE_STATE/gets"
"""
DPKG = """#!/bin/sh
# record the install: each .deb is named <sha>.deb, its package comes from $FIXTURE_NAMES
for f in "$@"; do
  sha=$(basename "$f" .deb)
  grep "^$sha " "$FIXTURE_NAMES" | cut -d' ' -f2- >> "$FIXTURE_STATE/installed"
done
echo "$@" >> "$FIXTURE_STATE/dpkg-calls"
"""
PRE_DEPENDS_DPKG = """#!/bin/sh
# like DPKG, but a package named python3 is refused until python3-minimal is installed
rc=0
for f in "$@"; do
  sha=$(basename "$f" .deb)
  name=$(grep "^$sha " "$FIXTURE_NAMES" | cut -d' ' -f2)
  if [ "$name" = python3 ] && ! grep -q "^python3-minimal " "$FIXTURE_STATE/installed"; then rc=1; continue; fi
  grep "^$sha " "$FIXTURE_NAMES" | cut -d' ' -f2- >> "$FIXTURE_STATE/installed"
done
echo "$@" >> "$FIXTURE_STATE/dpkg-calls"
exit $rc
"""
QUERY = """#!/bin/sh
# dpkg-query -W -f FMT NAME: "ii  <version>" for an installed name
name=$4
line=$(grep "^$name " "$FIXTURE_STATE/installed" 2>/dev/null | head -n1) || true
[ -n "$line" ] && echo "ii  ${line#* }"
exit 0
"""


def bash_ok() -> bool:
    if not BASH:
        return False
    r = subprocess.run([BASH, "-c", "mapfile -t a </dev/null; a=(); set -u; echo \"${a[@]}\""], capture_output=True)
    return r.returncode == 0


@unittest.skipUnless(bash_ok(), "needs bash 4.4+")
class Bootstrap(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for d in ("mirror", "state", "debs", "bin"):
            (self.tmp / d).mkdir()
        for name, text in (("get", GET), ("dpkg", DPKG), ("query", QUERY)):
            (self.tmp / "bin" / name).write_text(text)
            (self.tmp / "bin" / name).chmod(0o755)
        self.entries = []

    def package(self, name: str, version: str, body: bytes = b"", lock_sha: str = "") -> str:
        data = body or f"deb of {name} {version}".encode()
        sha = hashlib.sha256(data).hexdigest()
        (self.tmp / "mirror" / f"{name}_{version}_amd64.deb").write_bytes(data)
        self.entries.append((name, version, lock_sha or sha, sha))
        return sha

    def run_script(self, installed=()) -> subprocess.CompletedProcess:
        lock = ['snapshot = "20261118T000000Z"', 'architecture = "amd64"', "", "[tools]", 'release = "trixie"', "",
                "[[tools.package]]", 'name = "not-bootstrap"', 'version = "9"', f'sha256 = "{"0" * 64}"', 'archive = "debian"',
                'filename = "pool/main/n/not-bootstrap/not-bootstrap_9_amd64.deb"', "", "[bootstrap]", 'release = "trixie"']
        names = []
        for name, version, lock_sha, real_sha in self.entries:
            lock += ["", "[[bootstrap.package]]", f'name = "{name}"', f'version = "{version}"', f'sha256 = "{lock_sha}"',
                     'archive = "debian"', f'filename = "pool/main/{name[0]}/{name}/{name}_{version}_amd64.deb"',
                     'architecture = "amd64"', f'source = "{name}"']
            names.append(f"{lock_sha} {name} {version}")
        (self.tmp / "lock").write_text("\n".join(lock) + "\n")
        (self.tmp / "names").write_text("\n".join(names) + "\n")
        (self.tmp / "state" / "installed").write_text("".join(f"{n} {v}\n" for n, v in installed))
        env = dict(os.environ, NL_BOOTSTRAP_GET=str(self.tmp / "bin/get"), NL_BOOTSTRAP_DPKG=str(self.tmp / "bin/dpkg"),
                   NL_BOOTSTRAP_DPKG_QUERY=str(self.tmp / "bin/query"), NL_BOOTSTRAP_DIR=str(self.tmp / "debs"),
                   FIXTURE_MIRROR=str(self.tmp / "mirror"), FIXTURE_STATE=str(self.tmp / "state"), FIXTURE_NAMES=str(self.tmp / "names"))
        return subprocess.run([BASH, str(SCRIPT), str(self.tmp / "lock")], env=env, capture_output=True, text=True)

    def dpkg_calls(self) -> list[str]:
        f = self.tmp / "state" / "dpkg-calls"
        return f.read_text().splitlines() if f.exists() else []

    def test_installs_what_the_lock_names(self):
        self.package("python3", "3.13.5-1")
        self.package("gpgv", "2.4.7")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.dpkg_calls()), 1)
        self.assertEqual(len(self.dpkg_calls()[0].split()), 2)
        self.assertNotIn("not-bootstrap", (self.tmp / "state" / "gets").read_text())

    def test_wrong_hash_fails_before_dpkg(self):
        self.package("python3", "3.13.5-1", lock_sha="1" * 64)
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the lock pins", r.stderr)
        self.assertEqual(self.dpkg_calls(), [])

    def test_one_bad_package_stops_the_whole_install(self):
        self.package("python3", "3.13.5-1")
        self.package("gpgv", "2.4.7", lock_sha="2" * 64)
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.dpkg_calls(), [])

    def test_corrupted_download_is_not_kept(self):
        sha = self.package("python3", "3.13.5-1")
        (self.tmp / "debs" / f"{sha}.deb").write_bytes(b"truncated")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.tmp / "debs" / f"{sha}.deb").exists())
        self.assertEqual(self.dpkg_calls(), [])

    def test_installed_packages_are_left_alone(self):
        self.package("libc6", "2.41-12")
        self.package("python3", "3.13.5-1")
        r = self.run_script(installed=[("libc6", "2.41-11")])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.dpkg_calls()[0].split()), 1)
        self.assertNotIn("libc6", (self.tmp / "state" / "gets").read_text())

    def test_a_package_dpkg_did_not_install_fails(self):
        self.package("python3", "3.13.5-1")
        (self.tmp / "bin" / "dpkg").write_text("#!/bin/sh\nexit 0\n")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is not installed", r.stderr)

    def test_a_pre_depends_is_retried_in_a_later_call(self):
        self.package("python3", "3.13.5-1")
        self.package("python3-minimal", "3.13.5-1")
        (self.tmp / "bin" / "dpkg").write_text(PRE_DEPENDS_DPKG)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.dpkg_calls()), 2)
        self.assertEqual(len(self.dpkg_calls()[1].split()), 1)

    def test_a_lock_without_bootstrap_packages_fails(self):
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no [[bootstrap.package]]", r.stderr)

    def test_a_bad_lock_entry_fails(self):
        self.package("python3", "3.13.5-1", lock_sha="nothex")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("bad lock entry", r.stderr)


if __name__ == "__main__":
    unittest.main()
