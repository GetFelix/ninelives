"""Read an erofs image as a file, with no mount and no privilege.

The image checks need every inode's mode, owner, time, extended attributes and
contents. Mounting gives them but needs root, and extracting with fsck.erofs as
another user loses the owners and the attributes. This reads the format
directly (the kernel's erofs_fs.h): the superblock, compact and extended
inodes, plain and inline data, directories and extended attributes. It rejects
what the build never writes (compression, chunks, extra devices) instead of
guessing.
"""
import dataclasses
import pathlib
import stat
import struct

MAGIC = 0xE0F5E1E2
SB_OFFSET = 1024
FLAT_PLAIN, FLAT_INLINE = 0, 2
LAYOUTS = {0: "plain", 1: "compressed", 2: "inline", 3: "compressed", 4: "chunk"}
# Names of the standard xattr prefixes by name_index.
PREFIXES = {1: "user.", 2: "system.posix_acl_access", 3: "system.posix_acl_default", 4: "trusted.", 6: "security."}
LONG_PREFIX = 0x80
# The incompat feature bits the reader understands: long xattr name prefixes.
# Anything else changes how the image must be read (compression, devices,
# chunks, fragments, dedupe), so it is refused.
KNOWN_INCOMPAT = 0x40
# A directory loop in a damaged image would not end otherwise.
MAX_DEPTH = 64


class ErofsError(ValueError):
    pass


@dataclasses.dataclass
class Inode:
    nid: int
    mode: int
    uid: int
    gid: int
    size: int
    mtime: int
    layout: int
    raw_blkaddr: int
    xattrs: list
    data_at: int  # where an inline tail starts


class Image:
    def __init__(self, data: bytes):
        self.data = data
        if len(data) < SB_OFFSET + 128:
            raise ErofsError("the image is shorter than its superblock")
        sb = data[SB_OFFSET:SB_OFFSET + 128]
        magic, = struct.unpack_from("<I", sb, 0)
        if magic != MAGIC:
            raise ErofsError(f"bad magic {magic:#x}")
        feature_incompat, = struct.unpack_from("<I", sb, 80)
        extra_devices, = struct.unpack_from("<H", sb, 86)
        if feature_incompat & ~KNOWN_INCOMPAT:
            raise ErofsError(f"incompatible features {feature_incompat & ~KNOWN_INCOMPAT:#x} are not supported")
        if extra_devices:
            raise ErofsError("extra devices are not supported")
        self.blkbits = sb[12]
        self.blksz = 1 << self.blkbits
        self.root_nid, = struct.unpack_from("<H", sb, 14)
        self.inos, = struct.unpack_from("<Q", sb, 16)
        self.build_time, = struct.unpack_from("<Q", sb, 24)
        self.meta_blkaddr, = struct.unpack_from("<I", sb, 40)
        self.xattr_blkaddr, = struct.unpack_from("<I", sb, 44)
        self.prefix_count = sb[91]
        self.prefix_start, = struct.unpack_from("<I", sb, 92)
        self.feature_incompat = feature_incompat
        self._prefixes = None

    def _need(self, off: int, n: int) -> bytes:
        if off < 0 or off + n > len(self.data):
            raise ErofsError(f"read of {n} bytes at {off} is outside the image")
        return self.data[off:off + n]

    def long_prefix(self, idx: int) -> str:
        if self._prefixes is None:
            self._prefixes = []
            off = self.prefix_start * 4
            for _ in range(self.prefix_count):
                size, = struct.unpack_from("<H", self._need(off, 2))
                body = self._need(off + 2, size)  # base_index u8, infix
                self._prefixes.append(PREFIXES.get(body[0], f"#{body[0]}.") + body[1:].decode("utf-8", "replace"))
                off += (2 + size + 3) & ~3
        return self._prefixes[idx] if idx < len(self._prefixes) else f"#long{idx}."

    def inode(self, nid: int) -> Inode:
        at = self.meta_blkaddr * self.blksz + nid * 32
        fmt, icount = struct.unpack_from("<HH", self._need(at, 4))
        layout = (fmt >> 1) & 7
        if fmt & 1:
            h = struct.unpack_from("<HHHHQIIIIQII", self._need(at, 64))
            mode, size, raw, uid, gid, mtime = h[2], h[4], h[5], h[7], h[8], h[9]
            isize = 64
        else:
            h = struct.unpack_from("<HHHHIIIIHHI", self._need(at, 32))
            mode, size, raw, uid, gid, mtime = h[2], h[4], h[6], h[8], h[9], self.build_time
            isize = 32
        if layout not in (FLAT_PLAIN, FLAT_INLINE) and stat.S_IFMT(mode) in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise ErofsError(f"nid {nid} uses the {LAYOUTS.get(layout, layout)} layout, which this reader does not support")
        xsize = 12 + (icount - 1) * 4 if icount else 0
        xattrs = self._xattrs(at + isize, xsize) if xsize else []
        return Inode(nid, mode, uid, gid, size, mtime, layout, raw, xattrs, at + isize + xsize)

    def _xattrs(self, at: int, size: int) -> list:
        area = self._need(at, size)
        shared = area[4]
        names = []
        for i in range(shared):
            sid, = struct.unpack_from("<I", area, 12 + 4 * i)
            names.append(self._xattr_entry(self.xattr_blkaddr * self.blksz + sid * 4))
        off = 12 + 4 * shared
        while off + 4 <= size:
            nlen, index, vsize = struct.unpack_from("<BBH", area, off)
            names.append(self._name(index, area[off + 4:off + 4 + nlen]))
            off += (4 + nlen + vsize + 3) & ~3
        return names

    def _xattr_entry(self, at: int) -> str:
        nlen, index, _ = struct.unpack_from("<BBH", self._need(at, 4))
        return self._name(index, self._need(at + 4, nlen))

    def _name(self, index: int, name: bytes) -> str:
        if index & LONG_PREFIX:
            prefix = self.long_prefix(index & ~LONG_PREFIX)
        else:
            prefix = PREFIXES.get(index, f"unknown-prefix-{index}:")
        return prefix + name.decode("utf-8", "replace")

    def read(self, ino: Inode) -> bytes:
        """The file's bytes, or a directory's blocks, or a symlink's target."""
        full = ino.size // self.blksz if ino.layout == FLAT_INLINE else -(-ino.size // self.blksz)
        out = self._need(ino.raw_blkaddr * self.blksz, full * self.blksz) if full else b""
        if ino.layout == FLAT_INLINE:
            out += self._need(ino.data_at, ino.size % self.blksz)
        return out[:ino.size]

    def entries(self, ino: Inode) -> list[tuple[str, int]]:
        """(name, nid) of a directory's entries, without . and .."""
        out = []
        data = self.read(ino)
        for start in range(0, len(data), self.blksz):
            block = data[start:start + self.blksz]
            first, = struct.unpack_from("<H", block, 8)
            count = first // 12
            offs = [struct.unpack_from("<H", block, 12 * i + 8)[0] for i in range(count)]
            for i in range(count):
                nid, = struct.unpack_from("<Q", block, 12 * i)
                end = offs[i + 1] if i + 1 < count else len(block)
                name = block[offs[i]:end]
                if i + 1 == count:
                    name = name.split(b"\0", 1)[0]
                if name not in (b".", b".."):
                    out.append((name.decode("utf-8", "surrogateescape"), nid))
        return out


def walk(path: pathlib.Path, make_entry):
    """Call make_entry(path, kind, mode, uid, gid, size, xattrs, link, reader, mtime) for every inode, depth first."""
    img = Image(path.read_bytes())
    out = []
    seen_nids = []

    def visit(posix: str, nid: int, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise ErofsError(f"directories nest deeper than {MAX_DEPTH} at {posix}")
        ino = img.inode(nid)
        seen_nids.append(nid)
        fmt = stat.S_IFMT(ino.mode)
        kind = {stat.S_IFREG: "file", stat.S_IFDIR: "dir", stat.S_IFLNK: "symlink", stat.S_IFIFO: "fifo",
                stat.S_IFSOCK: "socket", stat.S_IFCHR: "chr", stat.S_IFBLK: "blk"}.get(fmt, "other")
        link = img.read(ino).decode("utf-8", "surrogateescape") if kind == "symlink" else ""
        reader = (lambda _p, i=ino: img.read(i)) if kind == "file" else None
        out.append(make_entry(posix or "/", kind, stat.S_IMODE(ino.mode), ino.uid, ino.gid, ino.size, ino.xattrs, link, reader, ino.mtime))
        if kind == "dir":
            for name, child in img.entries(ino):
                visit(f"{posix}/{name}", child, depth + 1)

    visit("", img.root_nid, 0)
    # An inode the walk never reached would go unchecked; the superblock counts them all.
    seen = len(set(seen_nids))
    if seen != img.inos:
        raise ErofsError(f"the walk found {seen} inodes, the superblock counts {img.inos}")
    return sorted(out, key=lambda e: e.path)
