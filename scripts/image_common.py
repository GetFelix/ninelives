"""Shared helpers for the image build scripts (docs/components/image.md).

Debian metadata parsing, dpkg version order, and checking a signed InRelease
with gpgv against the keys pinned in images/keys/debian/. Standard library
only: the fetch phase runs before anything else is trusted.
"""
import hashlib
import pathlib
import posixpath
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_kernel  # noqa: E402  (key and gpgv status helpers)

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGES = ROOT / "images"
SHA256 = re.compile(r"[0-9a-f]{64}")

dearmor = fetch_kernel.dearmor
valid_signers = fetch_kernel.valid_signers
load_fingerprints = fetch_kernel.load_fingerprints


def load_toml(path: pathlib.Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: pathlib.Path, attempts: int = 5) -> None:
    """Fetch url to dest. The caller verifies the bytes; nothing here trusts them."""
    if not url.startswith("https://") and not url.startswith("http://snapshot.debian.org/"):
        raise ValueError(f"refusing to download {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for n in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, part.open("wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            part.rename(dest)
            return
        except OSError:
            if n == attempts - 1:
                raise
            time.sleep(2 * (n + 1))


# ---------------------------------------------------------------------------
# Debian metadata


def parse_deb822(text: str) -> list[dict[str, str]]:
    """Paragraphs of a control-style file. Continuation lines are joined with newlines."""
    out, cur, key = [], {}, None
    for line in text.split("\n"):
        if not line.strip():
            if cur:
                out.append(cur)
            cur, key = {}, None
        elif line[0] in " \t":
            if key is None:
                raise ValueError("continuation line before a field")
            cur[key] += "\n" + line.strip()
        else:
            key, _, value = line.partition(":")
            cur[key] = value.strip()
    if cur:
        out.append(cur)
    return out


def verify_inrelease(path: pathlib.Path, keys: pathlib.Path, fprs: set[str]) -> str:
    """The signed text of an InRelease, only if a pinned key signed it.

    The text comes from gpgv's own output, so what is parsed is exactly what
    was signed and not whatever surrounds the signature block.
    """
    with tempfile.TemporaryDirectory() as d:
        keyring = pathlib.Path(d) / "keyring.gpg"
        keyring.write_bytes(b"".join(dearmor(p.read_text(encoding="ascii")) for p in sorted(keys.glob("*.asc"))))
        out = pathlib.Path(d) / "release"
        r = subprocess.run(
            ["gpgv", "--status-fd", "1", "--keyring", str(keyring), "--output", str(out), str(path)],
            capture_output=True, text=True,
        )
        signers, bad = valid_signers(r.stdout)
        # InRelease carries several signatures; some are by keys that are not
        # pinned (the previous release's). Those are ignored. A bad signature
        # by any key, or no valid one from a pinned key, fails.
        bad = [b for b in bad if b.split()[1] not in ("NO_PUBKEY", "ERRSIG")]
        if bad:
            raise ValueError(f"{path.name}: gpgv reported {' | '.join(bad)}")
        if not signers & fprs:
            raise ValueError(f"{path.name}: signed by {sorted(signers)}, none of them pinned in images/keys/debian/fingerprints")
        if not out.exists():
            raise ValueError(f"{path.name}: gpgv wrote no signed text ({r.stderr.strip()})")
        return out.read_text(encoding="utf-8")


def release_hashes(release_text: str) -> dict[str, tuple[str, int]]:
    """path -> (sha256, size) from a Release file's SHA256 block."""
    para = parse_deb822(release_text)[0]
    hashes = {}
    for line in para.get("SHA256", "").split("\n"):
        parts = line.split()
        if len(parts) == 3:
            hashes[parts[2]] = (parts[0], int(parts[1]))
    return hashes


def parse_packages(text: str) -> dict[str, dict[str, str]]:
    """Name -> stanza. Two stanzas for one name keep the higher version."""
    out: dict[str, dict[str, str]] = {}
    for p in parse_deb822(text):
        name = p["Package"]
        if name not in out or vercmp(p["Version"], out[name]["Version"]) > 0:
            out[name] = p
    return out


def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _vercmp_part(a: str, b: str) -> int:
    while a or b:
        i = 0
        # Non-digit prefix.
        while (i < len(a) and not a[i].isdigit()) or (i < len(b) and not b[i].isdigit()):
            ca = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            cb = _order(b[i]) if i < len(b) and not b[i].isdigit() else 0
            if ca != cb:
                return -1 if ca < cb else 1
            i += 1
        a, b = a[i:], b[i:]
        i = j = 0
        while i < len(a) and a[i].isdigit():
            i += 1
        while j < len(b) and b[j].isdigit():
            j += 1
        na, nb = int(a[:i] or 0), int(b[:j] or 0)
        if na != nb:
            return -1 if na < nb else 1
        a, b = a[i:], b[j:]
    return 0


def _split_version(v: str) -> tuple[int, str, str]:
    epoch = 0
    if ":" in v:
        e, v = v.split(":", 1)
        epoch = int(e)
    rev = ""
    if "-" in v:
        v, rev = v.rsplit("-", 1)
    return epoch, v, rev


def vercmp(a: str, b: str) -> int:
    """dpkg's version order: -1, 0 or 1."""
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _vercmp_part(ua, ub) or _vercmp_part(ra, rb)


def dep_alternatives(field: str) -> list[list[str]]:
    """'a (>= 1) | b, c:any' -> [['a', 'b'], ['c']]: package names only."""
    out = []
    for group in field.split(","):
        names = []
        for alt in group.split("|"):
            m = re.match(r"\s*([a-z0-9][a-z0-9+.-]*)", alt)
            if m:
                names.append(m.group(1))
        if names:
            out.append(names)
    return out


def provides(stanza: dict[str, str]) -> list[str]:
    return [m.group(1) for part in stanza.get("Provides", "").split(",") if (m := re.match(r"\s*([a-z0-9][a-z0-9+.-]*)", part))]


# ---------------------------------------------------------------------------
# .deb files and ELF files


class Member:
    """One path a package or archive puts in the root."""

    def __init__(self, path: str, kind: str, mode: int, sha256: str = "", link: str = ""):
        self.path, self.kind, self.mode, self.sha256, self.link = path, kind, mode, sha256, link

    def as_dict(self) -> dict:
        d = {"type": self.kind, "mode": self.mode}
        if self.sha256:
            d["sha256"] = self.sha256
        if self.link:
            d["link"] = self.link
        return d


def read_ar(data: bytes) -> dict[str, bytes]:
    if data[:8] != b"!<arch>\n":
        raise ValueError("not an ar archive")
    out, pos = {}, 8
    while pos < len(data):
        header = data[pos:pos + 60]
        if len(header) < 60 or header[58:60] != b"`\n":
            raise ValueError("bad ar header")
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        pos += 60
        out[name] = data[pos:pos + size]
        pos += size + (size & 1)
    return out


def deb_members(path: pathlib.Path) -> list[Member]:
    """Every path in a .deb's data archive with its type and SHA-256.

    Only xz-compressed data archives are read: Debian 13 ships those, and
    anything else is not what the lock was made from.
    """
    import io
    import tarfile

    parts = read_ar(path.read_bytes())
    data = parts.get("data.tar.xz")
    if data is None:
        raise ValueError(f"{path.name}: no data.tar.xz member (found {sorted(parts)})")
    out: dict[str, Member] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tar:
        for m in tar:
            p = posixpath.normpath("/" + m.name)
            mode = m.mode & 0o7777
            if m.isdir():
                out[p] = Member(p, "dir", mode)
            elif m.isreg():
                out[p] = Member(p, "file", mode, hashlib.sha256(tar.extractfile(m).read()).hexdigest())
            elif m.issym():
                out[p] = Member(p, "symlink", 0o777, link=m.linkname)
            elif m.islnk():
                target = posixpath.normpath("/" + m.linkname)
                src = out.get(target)
                if src is None or src.kind != "file":
                    raise ValueError(f"{path.name}: hard link {m.name} to {m.linkname} has no file before it")
                out[p] = Member(p, "file", src.mode, src.sha256)
            else:
                raise ValueError(f"{path.name}: {m.name} is a device, FIFO or other special file")
    return list(out.values())


class Elf:
    """Just enough of an ELF64 file for the image checks."""

    PT_LOAD, PT_DYNAMIC, PT_GNU_STACK = 1, 2, 0x6474E551

    def __init__(self, data: bytes):
        import struct

        if data[:4] != b"\x7fELF":
            raise ValueError("not ELF")
        if data[4] != 2 or data[5] != 1:
            raise ValueError("not 64-bit little-endian ELF")
        self.type, self.machine = struct.unpack_from("<HH", data, 16)
        phoff = struct.unpack_from("<Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 54)
        self.segments = []
        for i in range(phnum):
            p_type, p_flags, p_off, _va, _pa, p_filesz, _memsz, _al = struct.unpack_from("<IIQQQQQQ", data, phoff + i * phentsize)
            self.segments.append((p_type, p_flags, p_off, p_filesz, _va))
        self.executable_stack = any(s[0] == self.PT_GNU_STACK and s[1] & 1 for s in self.segments)
        self.writable_executable = any(s[0] == self.PT_LOAD and s[1] & 1 and s[1] & 2 for s in self.segments)
        self.needed = self._needed(data, struct)

    def _needed(self, data: bytes, struct) -> list[str]:
        dyn = next((s for s in self.segments if s[0] == self.PT_DYNAMIC), None)
        if dyn is None:
            return []
        shoff = struct.unpack_from("<Q", data, 40)[0]
        shentsize, shnum = struct.unpack_from("<HH", data, 58)
        strtab_off = None
        # DT_STRTAB is a virtual address; map it through the PT_LOAD segments.
        entries = []
        off = dyn[2]
        while off + 16 <= len(data):
            tag, val = struct.unpack_from("<qQ", data, off)
            if tag == 0:
                break
            entries.append((tag, val))
            off += 16
        strtab_va = next((v for t, v in entries if t == 5), None)
        if strtab_va is None:
            return []
        for s in self.segments:
            if s[0] == self.PT_LOAD and s[4] <= strtab_va < s[4] + s[3]:
                strtab_off = s[2] + (strtab_va - s[4])
        if strtab_off is None:
            raise ValueError("DT_STRTAB is outside every segment")
        names = []
        for tag, val in entries:
            if tag == 1:
                start = strtab_off + val
                names.append(data[start:data.index(b"\0", start)].decode("ascii"))
        return names
