"""scripts/erofs_read.py against an image mkfs.erofs wrote.

fixtures/sample.erofs came from mkfs.erofs 1.9 with `-b4096 -T1700000000` over a tree of: small
("hello\\n", hard-linked as hard, user.note xattr), big (10240 bytes, mode 4755), many/ (120
entries, so the directory spans blocks), dir/link -> ../small, and a fifo. It is owned by whoever
made it, so the tests compare owners only to each other.
"""
import collections
import pathlib
import struct
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import erofs_read as er  # noqa: E402

SAMPLE = pathlib.Path(__file__).resolve().parent / "fixtures" / "sample.erofs"
E = collections.namedtuple("E", "path kind mode uid gid size xattrs link reader mtime")


def entries():
    return {e.path: e for e in er.walk(SAMPLE, E)}


class ErofsRead(unittest.TestCase):
    def test_tree(self):
        t = entries()
        self.assertEqual(t["/small"].kind, "file")
        self.assertEqual(t["/dir/link"].kind, "symlink")
        self.assertEqual(t["/dir/link"].link, "../small")
        self.assertEqual(t["/fifo"].kind, "fifo")
        self.assertEqual(t["/many"].kind, "dir")
        self.assertEqual(len([p for p in t if p.startswith("/many/")]), 120)
        self.assertEqual(t["/"].kind, "dir")

    def test_modes_and_times(self):
        t = entries()
        self.assertEqual(t["/big"].mode, 0o4755)
        self.assertEqual(t["/small"].mtime, 1700000000)
        self.assertEqual(t["/big"].size, 10240)

    def test_contents_across_blocks_and_hard_links(self):
        t = entries()
        self.assertEqual(t["/small"].reader("/small"), b"hello\n")
        self.assertEqual(t["/hard"].reader("/hard"), b"hello\n")
        self.assertEqual(t["/big"].reader("/big"), bytes(range(256)) * 40)

    def test_xattrs_are_listed(self):
        t = entries()
        self.assertEqual(t["/small"].xattrs, ["user.note"])
        self.assertEqual(t["/big"].xattrs, [])

    def test_owner_is_read_from_the_image(self):
        t = entries()
        self.assertEqual({(e.uid, e.gid) for e in t.values()}, {(t["/"].uid, t["/"].gid)})

    def test_rejects_garbage(self):
        with self.assertRaises(er.ErofsError):
            er.Image(b"\0" * 4096)
        with self.assertRaises(er.ErofsError):
            er.Image(b"short")

    def test_inode_count_matches_the_superblock(self):
        # 3 hard-linked paths still count once per inode, so the walk has more paths than inodes.
        data = bytearray(SAMPLE.read_bytes())
        inos = struct.unpack_from("<Q", data, er.SB_OFFSET + 16)[0]
        self.assertLess(inos, len(entries()))
        struct.pack_into("<Q", data, er.SB_OFFSET + 16, inos + 1)
        tampered = pathlib.Path(tempfile.mkdtemp()) / "t.erofs"
        tampered.write_bytes(bytes(data))
        with self.assertRaises(er.ErofsError) as cm:
            er.walk(tampered, E)
        self.assertIn("superblock counts", str(cm.exception))

    def test_rejects_an_unknown_incompat_feature(self):
        data = bytearray(SAMPLE.read_bytes())
        struct.pack_into("<I", data, er.SB_OFFSET + 80, 0x1)  # lz4 0padding: a compressed image
        with self.assertRaises(er.ErofsError):
            er.Image(bytes(data))

    def test_an_unknown_xattr_prefix_is_named_unknown(self):
        img = er.Image(SAMPLE.read_bytes())
        self.assertEqual(img._name(5, b"x"), "unknown-prefix-5:x")
        self.assertEqual(img._name(1, b"x"), "user.x")

    def test_rejects_a_changed_layout(self):
        data = bytearray(SAMPLE.read_bytes())
        img = er.Image(bytes(data))
        at = img.meta_blkaddr * img.blksz + img.root_nid * 32
        data[at] = (data[at] & 1) | (1 << 1 * 0) | (4 << 1)  # chunk-based layout
        with self.assertRaises(er.ErofsError):
            er.Image(bytes(data)).inode(img.root_nid)


if __name__ == "__main__":
    unittest.main()
