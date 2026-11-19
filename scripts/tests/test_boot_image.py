"""The boot test's verdicts, its qemu command, the byte flip and the run loop against a fake qemu."""
import contextlib
import io
import os
import pathlib
import stat
import struct
import sys
import tempfile
import unittest
import uuid
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import boot_image as bi  # noqa: E402

READY = 'ninelives-init {"v":1,"boot":"0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d10","mono_ms":1234,"wall_ms":1763500000000,"machine":"up"}'
CMDLINE = ('Kernel command line: init=/usr/lib/ninelives/init console=ttyS0,115200 ro root=/dev/dm-0 '
           'dm-mod.create="root,,,ro,0 1 verity 1 /dev/vda2 /dev/vda3 4096 4096 9 0 sha256 ab cd 1 panic_on_corruption" panic=10 oops=panic')
HEALTHY = "\n".join([
    "[    0.000000] Linux version 6.18.64",
    "[    0.000000] " + CMDLINE,
    "[    0.50] device-mapper: verity: sha256 using implementation \"sha256-ni\"",
    "[    0.60] device-mapper: init: dm-0 is ready",
    "[    0.70] EROFS (device dm-0): mounted with root inode @ nid 36.",
])
VERITY_PANIC = "\n".join([
    HEALTHY.split("\n")[0], HEALTHY.split("\n")[1],
    "[    0.70] device-mapper: verity: 253:0: data block 0 is corrupted",
    "[    0.71] Kernel panic - not syncing: dm-verity device corrupted",
])
# What the kernel prints without panic_on_corruption: EIO to the reader, then the mount fails.
VERITY_EIO = "\n".join([
    HEALTHY.split("\n")[0], HEALTHY.split("\n")[1],
    "[    0.70] device-mapper: verity: 253:0: data block 0 is corrupted",
    "[    0.71] EROFS (device dm-0): cannot read erofs superblock",
    "[    0.72] VFS: Cannot open root device \"dm-0\" or unknown-block(253,0): error -5",
    "[    0.73] Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(253,0)",
])


class Verdicts(unittest.TestCase):
    def test_ready_line_after_a_clean_boot_passes(self):
        self.assertEqual(bi.verdict(HEALTHY + "\r\n" + READY + "\r\n", "ready"), [])

    def test_missing_ready_line_fails(self):
        self.assertTrue(any("ready line" in e for e in bi.verdict(HEALTHY, "ready")))

    def test_the_echoed_command_line_is_not_a_failure(self):
        self.assertEqual(bi.failures(HEALTHY), [])

    def test_a_panic_fails_even_after_the_ready_line(self):
        log = HEALTHY + "\n" + READY + "\n[ 9.0] Kernel panic - not syncing: Attempted to kill init!"
        self.assertTrue(any(e.startswith("panic") for e in bi.verdict(log, "ready")))

    def test_each_failure_kind_is_found(self):
        for kind, line in [
            ("verity", "[ 1.0] device-mapper: verity: 253:0: metadata block 3 is corrupted"),
            ("panic", "[ 1.0] Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(0,0)"),
            ("oops", "[ 1.0] Oops: 0000 [#1] SMP"),
            ("oops", "[ 1.0] BUG: unable to handle page fault"),
            ("firmware", "UEFI Interactive Shell v2.2"),
            ("mount", "[ 1.0] VFS: Unable to mount root fs on /dev/dm-0"),
        ]:
            self.assertEqual(bi.failures(line)[0][0], kind, line)

    def test_the_inits_own_records_are_not_scanned(self):
        rec = 'ninelives-init {"v":1,"machine":"emergency","detail":"verity error panic"}'
        self.assertEqual(bi.failures(rec), [])
        self.assertEqual(bi.failures('ninelives-init {"v":1,"mono_ms":5,"panic":"boom"}')[0][0], "init panic")
        self.assertIsNone(bi.ready_line(rec))

    def test_a_ready_line_must_start_the_line(self):
        self.assertIsNone(bi.ready_line("x " + READY))

    def test_a_corrupted_image_must_show_verity_and_never_ready(self):
        self.assertEqual(bi.verdict(VERITY_PANIC, "verity"), [])
        self.assertTrue(bi.verdict(HEALTHY, "verity"))
        self.assertTrue(any("ready" in e for e in bi.verdict(VERITY_PANIC + "\n" + READY, "verity")))

    def test_eio_then_a_root_mount_panic_is_not_the_verity_panic(self):
        errors = bi.verdict(VERITY_EIO, "verity")
        self.assertTrue(any("dm-verity device corrupted" in e for e in errors), errors)
        self.assertTrue(any("VFS" in e for e in errors), errors)

    def test_a_verity_line_without_the_panic_fails(self):
        log = "\n".join(VERITY_PANIC.split("\n")[:-1])
        self.assertTrue(any("device corrupted" in e for e in bi.verdict(log, "verity")))

    def test_a_metadata_block_line_counts(self):
        log = VERITY_PANIC.replace("data block 0", "metadata block 0")
        self.assertEqual(bi.verdict(log, "verity"), [])

    def test_the_panic_without_a_block_line_fails(self):
        log = "\n".join(l for l in VERITY_PANIC.split("\n") if "is corrupted" not in l)
        self.assertTrue(any("corrupted-block" in e for e in bi.verdict(log, "verity")))

    def test_an_init_state_other_than_up_after_up_fails_a_clean_boot(self):
        for state in ("emergency", "degraded"):
            rec = READY.replace('"up"', f'"{state}"')
            errors = bi.verdict(HEALTHY + "\n" + READY + "\n" + rec, "ready")
            self.assertTrue(any("init state" in e for e in errors), (state, errors))

    def test_records_before_up_and_repeated_up_are_fine(self):
        booting = READY.replace('"up"', '"booting"')
        self.assertEqual(bi.verdict(HEALTHY + "\n" + booting + "\n" + READY + "\n" + READY, "ready"), [])

    def test_an_unrelated_panic_is_not_a_verity_failure(self):
        log = HEALTHY + "\n[ 1.0] Kernel panic - not syncing: Fatal exception"
        self.assertTrue(any("verity" in e for e in bi.verdict(log, "verity")))


class Setup(unittest.TestCase):
    def test_accelerator_choice(self):
        self.assertEqual(bi.pick_accel("auto", True), "kvm")
        self.assertEqual(bi.pick_accel("auto", False), "tcg")
        self.assertEqual(bi.pick_accel("tcg", True), "tcg")
        with self.assertRaises(SystemExit):
            bi.pick_accel("kvm", False)

    def test_tcg_waits_longer_than_kvm(self):
        self.assertGreater(bi.TIMEOUTS["tcg"], bi.TIMEOUTS["kvm"])

    def test_qemu_command(self):
        p = pathlib.Path
        cmd = bi.qemu_command("qemu", "tcg", p("i.raw"), p("code.fd"), p("vars.fd"), p("s.log"), snapshot=True)
        self.assertIn("-no-reboot", cmd)
        self.assertEqual(cmd[cmd.index("-cpu") + 1], "max")
        self.assertIn("file:s.log", cmd)
        self.assertIn("if=pflash,format=raw,unit=0,readonly=on,file=code.fd", cmd)
        self.assertTrue(any(c.endswith("snapshot=on") for c in cmd))
        kvm = bi.qemu_command("qemu", "kvm", p("i.raw"), p("c"), p("v"), p("l"), snapshot=False)
        self.assertEqual(kvm[kvm.index("-cpu") + 1], "host")
        self.assertFalse(any("snapshot" in c for c in kvm))


def disk(path: pathlib.Path) -> None:
    """A GPT with the two partitions the flip needs, at sectors 34 and 42, a sector count of 8 each."""
    img = bytearray(512 * 64)
    img[512:520] = b"EFI PART"
    struct.pack_into("<QII", img, 512 + 72, 2, 128, 128)
    for i, (name, first) in enumerate([("ninelives-root-a", 34), ("ninelives-verity-a", 42)]):
        e = 512 * 2 + 128 * i
        img[e:e + 16] = uuid.UUID(int=1).bytes_le
        img[e + 16:e + 32] = uuid.UUID(int=10 + i).bytes_le
        struct.pack_into("<QQ", img, e + 32, first, first + 7)
        img[e + 56:e + 56 + len(name) * 2] = name.encode("utf-16-le")
    path.write_bytes(img)


class Flip(unittest.TestCase):
    def test_flips_one_byte_inside_block_zero_of_the_named_partition(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = pathlib.Path(d) / "a.raw", pathlib.Path(d) / "b.raw"
            disk(src)
            for which, start, inside in [("root", 34, 2048), ("hash", 42, 17)]:
                bi.corrupt_copy(src, dst, which)
                a, b = src.read_bytes(), dst.read_bytes()
                diff = [i for i in range(len(a)) if a[i] != b[i]]
                self.assertEqual(diff, [start * 512 + inside])
                self.assertEqual(b[diff[0]], a[diff[0]] ^ 0xFF)
                self.assertLess(inside, bi.BLOCK)

    def test_missing_partition_is_an_error(self):
        with self.assertRaises(ValueError):
            bi.flip_plan([], "root")


FAKE = """#!/usr/bin/env python3
import sys, time
args = sys.argv[1:]
log = args[args.index("-serial") + 1][len("file:"):]
script = open(__file__ + ".script").read().split("\\n")
with open(log, "w") as f:
    for l in script:
        if l.startswith("sleep "):
            time.sleep(float(l.split()[1]))
        elif l == "exit":
            sys.exit(0)
        else:
            f.write(l + "\\r\\n"); f.flush()
"""


class Run(unittest.TestCase):
    """bi.main against a stand-in for qemu that plays a console script."""

    def run_boot(self, script: list[str], *extra: str, timeout: str = "5"):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            fake = d / "qemu"
            fake.write_text(FAKE)
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            (d / "qemu.script").write_text("\n".join(script))
            for n in ("code.fd", "vars.fd"):
                (d / n).write_bytes(b"fw")
            disk(d / "i.raw")
            argv = ["boot_image.py", "--image", str(d / "i.raw"), "--ovmf-code", str(d / "code.fd"), "--ovmf-vars", str(d / "vars.fd"),
                    "--log", str(d / "out" / "serial.log"), "--qemu", str(fake), "--accel", "tcg", "--timeout", timeout, *extra]
            out = io.StringIO()
            with mock.patch.object(sys, "argv", argv), mock.patch.object(bi, "SETTLE", 0), contextlib.redirect_stdout(out):
                code = bi.main()
            return code, out.getvalue(), (d / "out" / "serial.log").read_text()

    def test_ready_boot_passes_and_keeps_the_log(self):
        code, out, log = self.run_boot([*HEALTHY.split("\n"), READY, "sleep 30"])
        self.assertEqual(code, 0, out)
        self.assertIn(READY, log)

    def test_panic_before_ready_fails(self):
        code, out, _ = self.run_boot([*VERITY_PANIC.split("\n"), "sleep 30"])
        self.assertEqual(code, 1)
        self.assertIn("panic", out)

    def test_firmware_shell_fails_at_once_in_both_modes(self):
        for extra in ((), ("--corrupt", "root")):
            code, out, _ = self.run_boot(["UEFI Interactive Shell v2.2", "sleep 30"], *extra)
            self.assertEqual(code, 1)
            self.assertIn("firmware", out)

    def test_silence_times_out(self):
        code, out, _ = self.run_boot(["sleep 30"], timeout="1")
        self.assertEqual(code, 1)
        self.assertIn("no answer within", out)

    def test_corrupted_boot_passes_when_verity_fails_and_the_vm_reboots(self):
        code, out, _ = self.run_boot([*VERITY_PANIC.split("\n"), "exit"], "--corrupt", "root")
        self.assertEqual(code, 0, out)
        self.assertIn("flipped one byte", out)

    def test_corrupted_boot_with_eio_and_a_mount_panic_fails(self):
        code, out, _ = self.run_boot([*VERITY_EIO.split("\n"), "exit"], "--corrupt", "root")
        self.assertEqual(code, 1)
        self.assertIn("VFS", out)

    def test_clean_boot_fails_if_qemu_exits_after_ready(self):
        code, out, _ = self.run_boot([*HEALTHY.split("\n"), READY, "exit"])
        self.assertEqual(code, 1)
        self.assertIn("qemu exited after the ready line", out)

    def test_clean_boot_fails_on_a_later_emergency_record(self):
        late = READY.replace('"up"', '"emergency"')
        with mock.patch.object(bi, "SETTLE", 2):
            code, out, _ = self.run_boot([*HEALTHY.split("\n"), READY, "sleep 0.3", late, "sleep 30"])
        self.assertEqual(code, 1)
        self.assertIn("init state", out)

    def test_corrupted_boot_fails_if_the_vm_does_not_reboot(self):
        with mock.patch.object(bi, "REBOOT_WAIT", 1):
            code, out, _ = self.run_boot([*VERITY_PANIC.split("\n"), "sleep 30"], "--corrupt", "hash")
        self.assertEqual(code, 1)
        self.assertIn("did not reboot", out)

    def test_corrupted_boot_fails_if_the_init_comes_up(self):
        code, out, _ = self.run_boot([*HEALTHY.split("\n"), READY, "sleep 30"], "--corrupt", "root")
        self.assertEqual(code, 1)
        self.assertIn("ready on a corrupted image", out)


if __name__ == "__main__":
    unittest.main()
