#!/usr/bin/env python3
"""Compare two builds of the image (docs/components/image.md, "Reproducibility").

  image_compare.py --a DIR --b DIR --report DIR

DIR is a build's output directory. Each holds SHA256SUMS over every output.
The two builds match only if both files are identical and every line in each
is true of the files next to it, so a stale or edited SHA256SUMS cannot pass.
On a mismatch this writes a summary and the per-partition hashes of any
differing disk, runs diffoscope on every output that differs, and exits 1.
The caller never retries.
"""
import argparse
import hashlib
import pathlib
import re
import shutil
import struct
import subprocess
import sys

SUM_LINE = re.compile(r"([0-9a-f]{64})  (\S[^/]*)")
DIFFOSCOPE_TIMEOUT = 1500


def read_sums(d: pathlib.Path) -> dict[str, str]:
    path = d / "SHA256SUMS"
    if not path.is_file():
        raise SystemExit(f"{path}: missing")
    sums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = SUM_LINE.fullmatch(line)
        if not m:
            raise SystemExit(f"{path}: not a sha256sum line: {line!r}")
        sums[m.group(2)] = m.group(1)
    return sums


def sha256(path: pathlib.Path, start: int = 0, length: int | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        f.seek(start)
        left = length
        while chunk := f.read(1 << 20 if left is None else min(1 << 20, left)):
            h.update(chunk)
            if left is not None:
                left -= len(chunk)
                if not left:
                    break
    return h.hexdigest()


def partitions(raw: pathlib.Path) -> list[tuple[str, int, int]]:
    """(name, first byte, length) of each GPT partition. 512-byte sectors, as the image is built."""
    with raw.open("rb") as f:
        f.seek(512)
        hdr = f.read(92)
        if hdr[:8] != b"EFI PART":
            return []
        entries_lba, count, size = struct.unpack_from("<QII", hdr, 72)
        f.seek(entries_lba * 512)
        table = f.read(count * size)
    out = []
    for i in range(count):
        e = table[i * size:(i + 1) * size]
        if e[:16] == bytes(16):
            continue
        first, last = struct.unpack_from("<QQ", e, 32)
        name = e[56:128].decode("utf-16-le").split("\x00")[0]
        out.append((name, first * 512, (last - first + 1) * 512))
    return out


def partition_table(a: pathlib.Path, b: pathlib.Path) -> list[str]:
    lines = []
    pa, pb = partitions(a), partitions(b)
    if [(n, s, ln) for n, s, ln in pa] != [(n, s, ln) for n, s, ln in pb]:
        return [f"{a.name}: the partition tables differ", f"  a: {pa}", f"  b: {pb}"]
    for name, start, length in pa:
        ha, hb = sha256(a, start, length), sha256(b, start, length)
        lines.append(f"{'same' if ha == hb else 'DIFFERS':8} {name} offset {start} length {length}")
        if ha != hb:
            lines.append(f"         a {ha}\n         b {hb}")
    return lines


def diffoscope(a: pathlib.Path, b: pathlib.Path, report: pathlib.Path) -> int:
    stem = report / a.name
    cmd = ["diffoscope", "--timeout", str(DIFFOSCOPE_TIMEOUT), "--text", f"{stem}.txt", "--html", f"{stem}.html", str(a), str(b)]
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, check=False).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", type=pathlib.Path, required=True)
    ap.add_argument("--b", type=pathlib.Path, required=True)
    ap.add_argument("--report", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.report.mkdir(parents=True, exist_ok=True)
    sums = {"a": read_sums(args.a), "b": read_sums(args.b)}
    problems = []
    for which, d in (("a", args.a), ("b", args.b)):
        files = {p.name for p in d.iterdir() if p.is_file() and p.name != "SHA256SUMS"}
        for name in sorted(files - set(sums[which])):
            problems.append(f"build {which}: {name} is not in its SHA256SUMS")
        for name, want in sorted(sums[which].items()):
            if not (d / name).is_file():
                problems.append(f"build {which}: {name} is in SHA256SUMS and not on disk")
            elif sha256(d / name) != want:
                problems.append(f"build {which}: {name} does not match its own SHA256SUMS line")
    differing = sorted(n for n in sums["a"].keys() & sums["b"].keys() if sums["a"][n] != sums["b"][n])
    for name in sorted(sums["a"].keys() ^ sums["b"].keys()):
        problems.append(f"{name} is an output of only one build")
    if not problems and not differing:
        print(f"the two builds match: {len(sums['a'])} outputs identical")
        return 0

    summary = ["The two builds differ.", ""] + problems
    for name in differing:
        summary.append(f"differs: {name}\n  a {sums['a'][name]}\n  b {sums['b'][name]}")
        if name.endswith(".raw") and (args.a / name).is_file() and (args.b / name).is_file():
            summary += ["  partitions:"] + ["    " + ln for ln in partition_table(args.a / name, args.b / name)]
    text = "\n".join(summary) + "\n"
    (args.report / "summary.txt").write_text(text, encoding="utf-8")
    print(text, file=sys.stderr)
    # Smaller outputs first: a report on the root filesystem explains the disk.
    for name in sorted(differing, key=lambda n: ((args.a / n).stat().st_size if (args.a / n).is_file() else 0, n)):
        if (args.a / name).is_file() and (args.b / name).is_file() and shutil.which("diffoscope"):
            diffoscope(args.a / name, args.b / name, args.report)
    return 1


if __name__ == "__main__":
    sys.exit(main())
