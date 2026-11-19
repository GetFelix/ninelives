"""Unit tests for scripts/image_compare.py: what counts as two matching builds."""
import hashlib
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import image_compare as cmp  # noqa: E402


def gpt(parts: list[tuple[str, bytes]]) -> bytes:
    """A disk with a GPT header at LBA 1, entries at LBA 2, and each partition's bytes (whole sectors)."""
    first = 34
    entries = b""
    body = b""
    lba = first
    for name, data in parts:
        data = data.ljust(-(-len(data) // 512) * 512, b"\0")
        e = bytearray(128)
        e[0:16] = b"\x01" * 16
        struct.pack_into("<QQ", e, 32, lba, lba + len(data) // 512 - 1)
        e[56:56 + 2 * len(name)] = name.encode("utf-16-le")
        entries += bytes(e)
        body += data
        lba += len(data) // 512
    entries = entries.ljust(128 * 128, b"\0")
    hdr = bytearray(92)
    hdr[0:8] = b"EFI PART"
    struct.pack_into("<QII", hdr, 72, 2, 128, 128)
    return bytes(512) + bytes(hdr).ljust(512, b"\0") + entries + bytes(512 * 34 - 1024 - len(entries)) + body


def build(d: pathlib.Path, files: dict[str, bytes], sums: dict[str, bytes] | None = None) -> pathlib.Path:
    d.mkdir(parents=True)
    for n, data in files.items():
        (d / n).write_bytes(data)
    shown = files if sums is None else sums
    (d / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(v).hexdigest()}  {n}\n" for n, v in sorted(shown.items())), encoding="utf-8")
    return d


def outputs(**changed: bytes) -> dict[str, bytes]:
    """A full set of outputs; changed maps a suffix (dots as underscores) to other bytes."""
    names = {"raw": "x.raw", "erofs": "x.root.erofs", "verity": "x.root.verity", "roothash": "x.roothash", "efi": "x.efi",
             "inventory": "x.inventory.json", "files": "x.files.json", "origins": "x.origins.json"}
    out = {n: n.encode() for n in names.values()}
    for k, v in changed.items():
        out[names[k]] = v
    return out


class Compare(unittest.TestCase):
    def run_compare(self, a: dict, b: dict, diffoscope_rc: int = 1, extra: list[str] | None = None, **kw) -> tuple[int, pathlib.Path]:
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        da, db = build(tmp / "a", a, kw.get("sums_a")), build(tmp / "b", b, kw.get("sums_b"))
        sys.argv = ["image_compare", "--a", str(da), "--b", str(db), "--report", str(tmp / "report"), *(extra or [])]
        self.diffoscope_calls = []

        def fake(pa, pb, report):
            self.diffoscope_calls.append(pa.name)
            return diffoscope_rc
        with mock.patch.object(cmp, "diffoscope", fake), mock.patch.object(cmp.shutil, "which", lambda n: "/usr/bin/" + n):
            return cmp.main(), tmp / "report"

    def test_identical_builds_match(self):
        code, report = self.run_compare(outputs(), outputs())
        self.assertEqual(code, 0)
        self.assertFalse((report / "summary.txt").exists())

    def test_two_empty_sums_do_not_match(self):
        code, report = self.run_compare({}, {})
        self.assertEqual(code, 1)
        self.assertIn("does not name exactly one output ending .raw", (report / "summary.txt").read_text())

    def test_a_missing_output_kind_fails_in_both_builds(self):
        a = outputs()
        del a["x.files.json"]
        code, report = self.run_compare(a, a)
        self.assertEqual(code, 1)
        self.assertIn("build a: SHA256SUMS does not name exactly one output ending .files.json", (report / "summary.txt").read_text())

    def test_one_differing_output_fails_and_is_named(self):
        code, report = self.run_compare(outputs(roothash=b"a"), outputs(roothash=b"b"))
        self.assertEqual(code, 1)
        text = (report / "summary.txt").read_text()
        self.assertIn("differs: x.roothash", text)
        self.assertNotIn("differs: x.efi", text)
        self.assertEqual(self.diffoscope_calls, ["x.roothash"])

    def test_a_diffoscope_that_does_not_report_differences_is_a_failure(self):
        for rc in (0, 2):
            code, report = self.run_compare(outputs(efi=b"a"), outputs(efi=b"b"), diffoscope_rc=rc)
            self.assertEqual(code, 2)
            self.assertIn(f"diffoscope exited {rc} on x.efi", (report / "summary.txt").read_text())

    def test_an_output_of_only_one_build_fails(self):
        b = outputs()
        a = {**b, "x.extra": b"3"}
        code, report = self.run_compare(a, b)
        self.assertEqual(code, 1)
        self.assertIn("x.extra is an output of only one build", (report / "summary.txt").read_text())

    def test_a_sums_file_that_lies_fails_even_when_both_agree(self):
        # Both SHA256SUMS name the same hash, but build B's file is not what its line says.
        code, report = self.run_compare(outputs(), outputs(efi=b"3"), sums_b=outputs())
        self.assertEqual(code, 1)
        self.assertIn("build b: x.efi does not match its own SHA256SUMS line", (report / "summary.txt").read_text())

    def test_a_file_missing_from_the_sums_fails(self):
        full = outputs()
        code, report = self.run_compare({**full, "y.json": b"4"}, {**full, "y.json": b"4"}, sums_a=full, sums_b=full)
        self.assertEqual(code, 1)
        self.assertIn("y.json is not in its SHA256SUMS", (report / "summary.txt").read_text())

    def test_the_report_starts_from_the_partition_that_differs(self):
        a = gpt([("esp", b"e" * 1024), ("root", b"r" * 1024)])
        b = gpt([("esp", b"e" * 1024), ("root", b"R" * 1024)])
        code, report = self.run_compare(outputs(raw=a), outputs(raw=b))
        self.assertEqual(code, 1)
        text = (report / "summary.txt").read_text()
        self.assertRegex(text, r"same\s+esp")
        self.assertRegex(text, r"DIFFERS\s+root")

    def kernel_and_init(self, tmp, kernel_b=b"k", init_b=b"i", drop=None):
        dirs = {}
        for n, data in (("ka", b"k"), ("kb", kernel_b), ("ia", b"i"), ("ib", init_b)):
            d = tmp / n
            d.mkdir()
            names = ("bzImage", "System.map", ".config") if n[0] == "k" else ("init",)
            for f in names:
                if not (drop == (n, f)):
                    (d / f).write_bytes(data)
            dirs[n] = d
        return ["--kernel-a", str(dirs["ka"]), "--kernel-b", str(dirs["kb"]), "--init-a", str(dirs["ia"]), "--init-b", str(dirs["ib"])]

    def test_kernel_and_init_must_match_too(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        code, _ = self.run_compare(outputs(), outputs(), extra=self.kernel_and_init(tmp))
        self.assertEqual(code, 0)

    def test_a_different_kernel_or_init_fails(self):
        for kb, ib, name in ((b"K", b"i", "kernel/bzImage"), (b"k", b"I", "init/init")):
            tmp = pathlib.Path(tempfile.mkdtemp())
            self.addCleanup(lambda t=tmp: __import__("shutil").rmtree(t, ignore_errors=True))
            code, report = self.run_compare(outputs(), outputs(), extra=self.kernel_and_init(tmp, kb, ib))
            self.assertEqual(code, 1)
            self.assertIn(f"differs: {name}", (report / "summary.txt").read_text())

    def test_a_missing_kernel_file_fails(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        code, report = self.run_compare(outputs(), outputs(), extra=self.kernel_and_init(tmp, drop=("kb", "System.map")))
        self.assertEqual(code, 1)
        self.assertIn("kernel: System.map is missing", (report / "summary.txt").read_text())


if __name__ == "__main__":
    unittest.main()
