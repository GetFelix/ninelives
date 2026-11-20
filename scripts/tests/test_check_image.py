"""One seeded violation per check in scripts/check_image.py, and a clean tree that passes them all."""
import pathlib
import struct
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tests"))
import check_image as ci  # noqa: E402
import image_assemble as asm  # noqa: E402
import image_common as ic  # noqa: E402
from test_image_common import elf  # noqa: E402

USERS = ic.load_toml(ic.IMAGES / "users.toml")
PASSWD = "".join(f"{u['name']}:x:{u['uid']}:{u['gid']}:{u['name']}:{u['home']}:{u['shell']}\n" for u in USERS["user"])
GROUP = "".join(f"{g['name']}:x:{g['gid']}:\n" for g in USERS["group"])


def clean():
    return [
        ci.Entry("/", "dir", 0o555),
        ci.Entry("/etc", "dir", 0o555),
        ci.Entry("/etc/passwd", "file", 0o444, content=PASSWD.encode()),
        ci.Entry("/etc/group", "file", 0o444, content=GROUP.encode()),
        ci.Entry("/usr", "dir", 0o555),
        ci.Entry("/usr/bin", "dir", 0o555),
        ci.Entry("/usr/bin/tool", "file", 0o555, content=elf()),
        ci.Entry("/usr/lib", "dir", 0o555),
        ci.Entry("/usr/lib/libx.so.1", "file", 0o444, content=elf(needed=())),
        ci.Entry("/usr/share", "dir", 0o555),
        ci.Entry("/usr/share/data", "file", 0o444, content=b"data"),
        ci.Entry("/bin", "symlink", 0o777, link="usr/bin"),
    ]


EXECS = [{"path": "/usr/bin/tool", "needed": ["libc.so.6"]}, {"path": "/usr/lib/*.so.*"}]


def with_(entries, *extra, drop=()):
    return [e for e in entries if e.path not in drop] + list(extra)


class Checks(unittest.TestCase):
    def test_clean_tree_passes_every_check(self):
        e = clean()
        self.assertEqual(ci.check_setuid(e) + ci.check_xattrs(e) + ci.check_ownership(e) + ci.check_special(e, {}), [])
        self.assertEqual(ci.check_users(e, USERS) + ci.check_executables(e, EXECS) + ci.check_elf(e) + ci.check_needed(e, EXECS), [])
        self.assertEqual(ci.check_secrets(e), [])

    def test_setuid_file(self):
        self.assertTrue(ci.check_setuid(with_(clean(), ci.Entry("/usr/bin/su", "file", 0o4555))))

    def test_setgid_directory(self):
        self.assertTrue(ci.check_setuid(with_(clean(), ci.Entry("/var", "dir", 0o2555))))

    def test_timestamp_not_source_date_epoch(self):
        self.assertEqual(ci.check_times([ci.Entry("/a", "file", mtime=5)], 5), [])
        self.assertTrue(ci.check_times([ci.Entry("/a", "file", mtime=6)], 5))

    def test_file_capability_attribute(self):
        self.assertTrue(ci.check_xattrs(with_(clean(), ci.Entry("/usr/bin/ping", "file", 0o555, xattrs=["security.capability"]))))

    def test_write_bit(self):
        self.assertTrue(ci.check_ownership(with_(clean(), ci.Entry("/etc/x", "file", 0o644))))

    def test_world_writable(self):
        out = ci.check_ownership(with_(clean(), ci.Entry("/tmp", "dir", 0o1777)))
        self.assertTrue(any("world-writable" in o for o in out))

    def test_file_owned_by_uid_1(self):
        self.assertTrue(ci.check_ownership(with_(clean(), ci.Entry("/etc/x", "file", 0o444, uid=1))))

    def test_fifo(self):
        self.assertTrue(ci.check_special(with_(clean(), ci.Entry("/run/x", "fifo")), {}))

    def test_relative_link_inside_the_root_passes(self):
        e = with_(clean(), ci.Entry("/usr/share/doc", "dir", 0o555), ci.Entry("/usr/share/doc/README", "file", 0o444),
                  ci.Entry("/usr/share/doc/FAQ", "symlink", 0o777, link="README"),
                  ci.Entry("/usr/lib/up", "symlink", 0o777, link="../share/data"))
        self.assertEqual(ci.check_special(e, {}), [])

    def test_dangling_relative_link_fails(self):
        self.assertTrue(ci.check_special(with_(clean(), ci.Entry("/lib32", "symlink", 0o777, link="usr/lib32")), {}))

    def test_link_into_writable_storage(self):
        e = with_(clean(), ci.Entry("/data", "dir", 0o555), ci.Entry("/etc/l", "symlink", 0o777, link="/data/x"),
                  ci.Entry("/data/x", "file", 0o444))
        self.assertTrue(ci.check_special(e, {}))

    def test_link_out_of_root_needs_listing(self):
        e = with_(clean(), ci.Entry("/etc/resolv.conf", "symlink", 0o777, link="/run/resolv/resolv.conf"))
        self.assertTrue(ci.check_special(e, {}))
        self.assertEqual(ci.check_special(e, {"/etc/resolv.conf": "/run/resolv/resolv.conf"}), [])

    def test_extra_passwd_entry(self):
        e = with_(clean(), ci.Entry("/etc/passwd", "file", 0o444, content=(PASSWD + "evil:x:1:1::/:/bin/sh\n").encode()),
                  drop=["/etc/passwd"])
        self.assertTrue(ci.check_users(e, USERS))

    def test_unlocked_shadow_entry(self):
        e = with_(clean(), ci.Entry("/etc/shadow", "file", 0o444, content=b"root:$6$abc:1::::::\n"))
        self.assertTrue(ci.check_users(e, USERS))

    def test_script_with_shebang(self):
        e = with_(clean(), ci.Entry("/usr/bin/run", "file", 0o555, content=b"#!/bin/sh\n"))
        out = ci.check_executables(e, EXECS)
        self.assertTrue(any("starts with #!" in o for o in out))

    def test_dash_binary(self):
        e = with_(clean(), ci.Entry("/usr/bin/dash", "file", 0o555, content=elf()))
        self.assertTrue(any("shell or interpreter" in o for o in ci.check_executables(e, EXECS + [{"path": "/usr/bin/dash"}])))

    def test_shell_in_allowlist_is_refused(self):
        self.assertTrue(any("shell or interpreter" in o for o in ci.check_executables(clean(), EXECS + [{"path": "/usr/bin/bash"}])))

    def test_unlisted_executable(self):
        e = with_(clean(), ci.Entry("/usr/bin/other", "file", 0o555, content=elf()))
        self.assertTrue(any("not in images/executables.toml" in o for o in ci.check_executables(e, EXECS)))

    def test_stale_allowlist_entry(self):
        self.assertTrue(ci.check_executables(clean(), EXECS + [{"path": "/usr/bin/gone"}]))

    def test_executable_stack(self):
        e = with_(clean(), ci.Entry("/usr/lib/liby.so.1", "file", 0o444, content=elf(flags_stack=7)))
        self.assertTrue(ci.check_elf(e))

    def test_pruned_library_something_still_needs(self):
        e = with_(clean(), ci.Entry("/usr/bin/tool", "file", 0o555, content=elf(needed=("libgone.so.1",))), drop=["/usr/bin/tool"])
        self.assertTrue(ci.check_needed_resolve(e))
        self.assertEqual(ci.check_needed_resolve(with_(e, ci.Entry("/usr/lib/libgone.so.1", "file", 0o444, content=elf(needed=())))), [])

    def test_changed_dt_needed(self):
        e = with_(clean(), ci.Entry("/usr/bin/tool", "file", 0o555, content=elf(needed=("libc.so.6", "libssl.so.3"))), drop=["/usr/bin/tool"])
        self.assertTrue(ci.check_needed(e, EXECS))

    def stage_for(self, installed, keep_names):
        return {"installed": [{"name": n, "version": "1", "sha256": "a" * 64, "kept": n in keep_names} for n in installed]}

    def lock_for(self, names):
        return {"root": {"package": [{"name": n, "version": "1", "sha256": "a" * 64} for n in names]}}

    def test_package_sets_that_match_pass(self):
        stage = self.stage_for(["libc6", "dash"], {"libc6"})
        self.assertEqual(ci.check_packages(stage, [{"name": "libc6"}], {"dash": "shell"}, self.lock_for(["libc6", "dash"])), [])

    def test_new_dependency_is_not_dropped_silently(self):
        # libc6 gained a dependency: installed, in neither list. The build used to remove it and pass.
        stage = self.stage_for(["libc6", "dash", "libnew"], {"libc6"})
        out = ci.check_packages(stage, [{"name": "libc6"}], {"dash": "shell"}, self.lock_for(["libc6", "dash", "libnew"]))
        self.assertTrue(any("libnew" in o for o in out))

    def test_missing_package_and_stale_removal(self):
        stage = self.stage_for(["libc6"], {"libc6"})
        out = ci.check_packages(stage, [{"name": "libc6"}, {"name": "base-files"}], {"dash": "shell"}, self.lock_for(["libc6"]))
        self.assertTrue(any("base-files" in o for o in out) and any("dash" in o for o in out))

    def test_lock_mismatch(self):
        stage = self.stage_for(["libc6"], {"libc6"})
        lock = {"root": {"package": [{"name": "libc6", "version": "2", "sha256": "a" * 64}]}}
        self.assertTrue(ci.check_packages(stage, [{"name": "libc6"}], {}, lock))

    def test_package_manager_kept_is_refused(self):
        stage = self.stage_for(["apt"], {"apt"})
        self.assertTrue(any("package manager" in o for o in ci.check_packages(stage, [{"name": "apt"}], {}, self.lock_for(["apt"]))))

    def test_repository_removals_and_packages_are_disjoint_and_cover_the_lock(self):
        removals = ic.load_removals(ic.ROOT / "rootfs" / "removals.toml")
        keep = {p["name"] for p in ic.load_toml(ic.IMAGES / "packages.toml")["package"]}
        lock = {p["name"] for p in ic.load_toml(ic.IMAGES / "debian.lock")["root"]["package"]}
        self.assertEqual(ic.package_set_errors(lock, keep, removals), [])

    def origins(self, entries):
        return {e.path: {"type": e.kind, "package": "p", "sha256": e.sha256() if e.kind == "file" else "", "link": e.link}
                for e in entries if e.kind != "dir"}

    def test_every_file_has_an_origin(self):
        e = clean()
        self.assertEqual(ci.check_origins(e, self.origins(e)), [])
        o = self.origins(e)
        del o["/usr/share/data"]
        self.assertTrue(ci.check_origins(e, o))

    def test_changed_file_has_no_matching_origin(self):
        e = clean()
        o = self.origins(e)
        o["/usr/share/data"]["sha256"] = "0" * 64
        self.assertTrue(ci.check_origins(e, o))

    def test_empty_unexplained_directory(self):
        e = with_(clean(), ci.Entry("/opt", "dir", 0o555))
        self.assertTrue(ci.check_origins(e, self.origins(clean())))

    def test_planted_secret(self):
        e = with_(clean(), ci.Entry("/etc/key", "file", 0o444, content=b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc"))
        self.assertTrue(ci.check_secrets(e))
        e = with_(clean(), ci.Entry("/etc/tok", "file", 0o444, content=b"token ghp_" + b"a" * 36))
        self.assertTrue(ci.check_secrets(e))

    def test_changed_command_line(self):
        policy = ci.kc.load_policy(ci.kc.KERNEL / "policy.toml", "x86_64")
        target = ic.load_toml(ic.IMAGES / "qemu-x86_64" / "target.toml")
        good = asm.cmdline(target, policy, 16, 2, "a" * 64, "b" * 64)
        self.assertEqual(ci.check_cmdline(good, good, policy), [])
        self.assertTrue(ci.check_cmdline(good + " mitigations=off", good, policy))
        self.assertTrue(ci.check_cmdline(good.replace(" oops=panic", ""), good, policy))

    def test_esp_with_an_extra_file(self):
        self.assertEqual(ci.check_esp(["EFI", "EFI/BOOT", "EFI/BOOT/BOOTX64.EFI"], b"u", b"u"), [])
        self.assertTrue(ci.check_esp(["EFI", "EFI/BOOT", "EFI/BOOT/BOOTX64.EFI", "loader/loader.conf"], b"u", b"u"))
        self.assertTrue(ci.check_esp(["EFI", "EFI/BOOT", "EFI/BOOT/BOOTX64.EFI"], b"x", b"u"))

    def test_esp_sector_size_must_be_512(self):
        def sector(n):
            b = bytearray(512)
            struct.pack_into("<H", b, 11, n)
            b[510:512] = b"\x55\xaa"
            return bytes(b)
        self.assertEqual(ci.check_esp_sector(sector(512)), [])
        self.assertTrue(any("4096" in e for e in ci.check_esp_sector(sector(4096))))
        self.assertTrue(ci.check_esp_sector(b"\0" * 512))

    def test_oversized_root(self):
        self.assertEqual(ci.check_budget({"root_erofs": 10}, {"root_erofs": 20}), [])
        self.assertTrue(ci.check_budget({"root_erofs": 30}, {"root_erofs": 20}))

    GPT_SIZES = {"esp_mib": 256, "config_mib": 1, "data_mib": 128}

    def parts(self):
        sz = {"esp": 256 << 20, "ninelives-root-a": 4 << 20, "ninelives-verity-a": 1 << 20, "ninelives-root-b": 4 << 20,
              "ninelives-verity-b": 1 << 20, "ninelives-config": 1 << 20, "ninelives-data": 128 << 20}
        return [{"name": n, "type": t, "size": sz[n]} for n, t in ci.GPT_PARTITIONS]

    def test_partition_table(self):
        self.assertEqual(ci.check_gpt(self.parts(), self.GPT_SIZES, True), [])

    def test_wrong_partition_table(self):
        parts = self.parts()
        self.assertTrue(ci.check_gpt(parts[:-1], self.GPT_SIZES, True))
        parts[1] = {**parts[1], "type": "0" * 8 + "-0000-0000-0000-000000000000"}
        self.assertTrue(ci.check_gpt(parts, self.GPT_SIZES, True))

    def test_esp_and_config_sizes_are_fixed(self):
        for name in ("esp", "ninelives-config"):
            parts = [{**p, "size": p["size"] * 2} if p["name"] == name else p for p in self.parts()]
            self.assertTrue(any(name in o for o in ci.check_gpt(parts, self.GPT_SIZES, True)), name)

    def test_b_slot_must_match_a(self):
        parts = [{**p, "size": 5 << 20} if p["name"] == "ninelives-root-b" else p for p in self.parts()]
        self.assertTrue(ci.check_gpt(parts, self.GPT_SIZES, True))

    def test_config_partition_must_be_zeros(self):
        self.assertTrue(any("zeros" in o for o in ci.check_gpt(self.parts(), self.GPT_SIZES, False)))

    def uki(self, **extra):
        return {".linux": b"kernel", ".cmdline": b"x", ".osrel": b"y", ".sbat": b"z", ".text": b"", ".uname": b"6", **extra}

    def test_uki_sections(self):
        import hashlib
        k = hashlib.sha256(b"kernel").hexdigest()
        self.assertEqual(ci.check_uki_sections(self.uki(), k), [])
        for sec in (".initrd", ".ucode", ".dtb", ".profile", ".pcrsig"):
            self.assertTrue(ci.check_uki_sections(self.uki(**{sec: b"x"}), k), sec)
        self.assertTrue(ci.check_uki_sections({k_: v for k_, v in self.uki().items() if k_ != ".sbat"}, k))

    def test_uki_kernel_must_be_the_artifacts(self):
        self.assertTrue(ci.check_uki_sections(self.uki(), "0" * 64))

    def test_glob_star_stops_at_slash(self):
        self.assertTrue(ci.glob_match("/usr/lib/x.so", "/usr/lib/*.so"))
        self.assertFalse(ci.glob_match("/usr/lib/sub/x.so", "/usr/lib/*.so"))

    def test_nested_file_is_not_let_in_by_a_star(self):
        e = with_(clean(), ci.Entry("/usr/lib/deep/er.so.1", "file", 0o444, content=elf(needed=())))
        out = ci.check_executables(e, EXECS)
        self.assertTrue(any("not in images/executables.toml" in o for o in out))

    def test_interpreter_matched_by_a_pattern_is_refused(self):
        for name in ("python3.13", "bash", "perl5.40.1", "busybox", "sh", "mawk"):
            e = with_(clean(), ci.Entry("/usr/bin/" + name, "file", 0o555, content=elf()))
            out = ci.check_executables(e, EXECS + [{"path": "/usr/bin/*"}])
            self.assertTrue(any("shell or interpreter: /usr/bin/" + name in o for o in out), name)


class ReadTree(unittest.TestCase):
    """The checks see an erofs image without mounting it: modes, owners and xattrs come from the format."""
    SAMPLE = pathlib.Path(__file__).resolve().parent / "fixtures" / "sample.erofs"

    def test_the_seeded_violations_are_found_in_a_real_image(self):
        entries = ci.read_tree(self.SAMPLE)
        self.assertTrue(any("/big" in v and "4755" in v for v in ci.check_setuid(entries)))
        self.assertTrue(any("/small" in v and "user.note" in v for v in ci.check_xattrs(entries)))
        self.assertTrue(ci.check_ownership(entries))
        self.assertTrue(ci.check_times(entries, 1))
        self.assertEqual(ci.check_times(entries, 1700000000), [])

    def test_contents_are_readable_for_the_content_checks(self):
        entries = {e.path: e for e in ci.read_tree(self.SAMPLE)}
        self.assertEqual(entries["/small"].content(), b"hello\n")
        self.assertEqual(len(entries["/big"].sha256()), 64)


class Formats(unittest.TestCase):
    def test_dm_create_roundtrip(self):
        policy = ci.kc.load_policy(ci.kc.KERNEL / "policy.toml", "x86_64")
        target = ic.load_toml(ic.IMAGES / "qemu-x86_64" / "target.toml")
        line = asm.cmdline(target, policy, 800, 100, "a" * 64, "b" * 64)
        self.assertEqual(ci.dm_create(line), {"sectors": 800, "blocks": 100, "roothash": "a" * 64, "salt": "b" * 64})
        self.assertTrue(line.startswith("init=/usr/lib/ninelives/init console=ttyS0,115200 ro rootfstype=erofs root=/dev/dm-0 "))

    def test_pe_sections(self):
        pe = 0x80
        data = bytearray(0x400)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, pe)
        data[pe:pe + 4] = b"PE\0\0"
        struct.pack_into("<H", data, pe + 6, 1)
        struct.pack_into("<H", data, pe + 20, 0)
        sec = pe + 24
        data[sec:sec + 8] = b".cmdline"
        struct.pack_into("<IIII", data, sec + 8, 5, 0, 512, 0x200)
        data[0x200:0x205] = b"hello"
        self.assertEqual(ci.pe_sections(bytes(data)), {".cmdline": b"hello"})

    def test_duplicate_pe_section_is_refused(self):
        pe = 0x80
        data = bytearray(0x600)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, pe)
        data[pe:pe + 4] = b"PE\0\0"
        struct.pack_into("<H", data, pe + 6, 2)
        for i, off in enumerate((0x200, 0x300)):
            sec = pe + 24 + i * 40
            data[sec:sec + 8] = b".cmdline"
            struct.pack_into("<IIII", data, sec + 8, 5, 0, 256, off)
        with self.assertRaises(ValueError):
            ci.pe_sections(bytes(data))

    def test_gpt_reader(self):
        import uuid
        img = bytearray(512 * 40)
        img[512:520] = b"EFI PART"
        struct.pack_into("<QII", img, 512 + 72, 2, 128, 128)
        e = 512 * 2
        img[e:e + 16] = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").bytes_le
        img[e + 16:e + 32] = uuid.UUID(int=5).bytes_le
        struct.pack_into("<QQ", img, e + 32, 34, 37)
        img[e + 56:e + 56 + 8] = "esp".encode("utf-16-le")
        with tempfile.NamedTemporaryFile() as f:
            f.write(img)
            f.flush()
            self.assertEqual(ci.read_gpt(pathlib.Path(f.name)),
                             [{"type": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "uuid": str(uuid.UUID(int=5)), "name": "esp",
                               "offset": 34 * 512, "size": 4 * 512}])

    def test_derived_values_are_stable(self):
        self.assertEqual(asm.derived_uuid("6c8d5c3e-9a64-4e0b-8f41-2b7a6f0c1d11", "x86_64", "disk"),
                         asm.derived_uuid("6c8d5c3e-9a64-4e0b-8f41-2b7a6f0c1d11", "x86_64", "disk"))
        self.assertNotEqual(asm.verity_salt("a" * 40), asm.verity_salt("b" * 40))
        self.assertEqual(len(asm.verity_salt("a" * 40)), 64)


if __name__ == "__main__":
    unittest.main()
