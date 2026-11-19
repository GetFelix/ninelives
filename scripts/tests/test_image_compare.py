"""Unit tests for scripts/image_compare.py: what counts as two matching builds."""
import hashlib
import pathlib
import struct
import sys
import tempfile
import unittest

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


class Compare(unittest.TestCase):
    def run_compare(self, a: dict, b: dict, **kw) -> tuple[int, pathlib.Path]:
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        da, db = build(tmp / "a", a, kw.get("sums_a")), build(tmp / "b", b, kw.get("sums_b"))
        sys.argv = ["image_compare", "--a", str(da), "--b", str(db), "--report", str(tmp / "report")]
        return cmp.main(), tmp / "report"

    def test_identical_builds_match(self):
        files = {"x.raw": b"1", "x.efi": b"2"}
        code, report = self.run_compare(files, dict(files))
        self.assertEqual(code, 0)
        self.assertFalse((report / "summary.txt").exists())

    def test_one_differing_output_fails_and_is_named(self):
        code, report = self.run_compare({"x.efi": b"2", "x.roothash": b"a"}, {"x.efi": b"2", "x.roothash": b"b"})
        self.assertEqual(code, 1)
        text = (report / "summary.txt").read_text()
        self.assertIn("differs: x.roothash", text)
        self.assertNotIn("differs: x.efi", text)

    def test_an_output_of_only_one_build_fails(self):
        code, report = self.run_compare({"x.efi": b"2", "x.extra": b"3"}, {"x.efi": b"2"})
        self.assertEqual(code, 1)
        self.assertIn("x.extra is an output of only one build", (report / "summary.txt").read_text())

    def test_a_sums_file_that_lies_fails_even_when_both_agree(self):
        # Both SHA256SUMS name the same hash, but build B's file is not what its line says.
        code, report = self.run_compare({"x.efi": b"2"}, {"x.efi": b"3"}, sums_b={"x.efi": b"2"})
        self.assertEqual(code, 1)
        self.assertIn("build b: x.efi does not match its own SHA256SUMS line", (report / "summary.txt").read_text())

    def test_a_file_missing_from_the_sums_fails(self):
        code, report = self.run_compare({"x.efi": b"2", "y.json": b"4"}, {"x.efi": b"2", "y.json": b"4"},
                                        sums_a={"x.efi": b"2"}, sums_b={"x.efi": b"2"})
        self.assertEqual(code, 1)
        self.assertIn("y.json is not in its SHA256SUMS", (report / "summary.txt").read_text())

    def test_the_report_starts_from_the_partition_that_differs(self):
        a = gpt([("esp", b"e" * 1024), ("root", b"r" * 1024)])
        b = gpt([("esp", b"e" * 1024), ("root", b"R" * 1024)])
        code, report = self.run_compare({"x.raw": a}, {"x.raw": b})
        self.assertEqual(code, 1)
        text = (report / "summary.txt").read_text()
        self.assertRegex(text, r"same\s+esp")
        self.assertRegex(text, r"DIFFERS\s+root")


if __name__ == "__main__":
    unittest.main()
