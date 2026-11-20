#!/usr/bin/env python3
"""Boot a built image in qemu with UEFI and read the verdict off the serial console.

  boot_image.py --image X.raw --ovmf-code F --ovmf-vars F --log serial.log
                [--corrupt root|hash] [--accel auto|kvm|tcg] [--timeout SECONDS]
                [--arch x86_64|aarch64] [--qemu BINARY]

--arch picks the machine: q35 for x86_64, virt for aarch64. The firmware files
are OVMF's for x86_64 and AAVMF's for aarch64 (--ovmf-code and --ovmf-vars name
whichever the architecture uses).

Without --corrupt the image must reach the init's ready line and nothing on the
console may be a kernel panic, an oops or a dm-verity error. With --corrupt one
byte of a block the boot reads is flipped in a copy of the image first, and the
boot must end in a dm-verity failure and a reboot, never the ready line
(docs/components/image.md, "Tests"). Exit status 0 means the expectation held.
The serial log is written on every outcome.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_image  # noqa: E402  (read_gpt)

# The init's ready line (init/README.md, "The ready line").
READY = re.compile(r'^ninelives-init \{.*"machine":"up"')
# Lines that end a boot, in the order the report names them.
FAILURES = [
    # Not just "verity": the kernel echoes the command line, which says verity and panic_on_corruption.
    ("verity", re.compile(r"(device-mapper: verity|dm-verity).*(corrupt|error|fail|mismatch|invalid)", re.I)),
    ("panic", re.compile(r"Kernel panic - not syncing")),
    ("oops", re.compile(r"\b(Oops|BUG):|general protection fault")),
    ("mount", re.compile(r"VFS: Unable to mount root fs")),
    # Firmware found nothing to boot and dropped to its shell: it would wait there until the timeout.
    ("firmware", re.compile(r"UEFI Interactive Shell")),
]
BLOCK = 4096
# Block 0 of each partition is read on every boot: the erofs superblock is in
# it, and verifying it reads the hash tree's first block. The offset only has
# to be inside the block.
FLIP = {"root": ("ninelives-root-a", 2048), "hash": ("ninelives-verity-a", 17)}
# Seconds. Emulating aarch64 on any host is slower than emulating x86_64.
TIMEOUTS = {"x86_64": {"kvm": 180, "tcg": 900}, "aarch64": {"kvm": 180, "tcg": 1500}}
MACHINES = {
    # (-machine type, -cpu under KVM, -cpu under TCG)
    "x86_64": ("q35", "host", "max"),
    # pauth-impdef makes TCG's pointer authentication cheap; the kernel uses it.
    "aarch64": ("virt", "host", "max,pauth-impdef=on"),
}
QEMU = {"x86_64": "qemu-system-x86_64", "aarch64": "qemu-system-aarch64"}
SETTLE = 3  # seconds to keep watching after the ready line, for a late panic
REBOOT_WAIT = 40  # panic=10 reboots after 10 s; the rest is slack for TCG


# What dm-verity prints with panic_on_corruption: the block, then this panic.
# A root that fails to mount some other way (EIO, a missing device) must not pass.
VERITY_BLOCK = re.compile(r"device-mapper: verity: \S+: (data|metadata) block \d+ is corrupted")
VERITY_PANIC = re.compile(r"^(\[[^\]]*\]\s*)?Kernel panic - not syncing: dm-verity device corrupted$")


def lines(log: str) -> list[str]:
    return [l.rstrip("\r") for l in log.split("\n")]


def ready_line(log: str) -> str | None:
    return next((l for l in lines(log) if READY.match(l)), None)


def record_state(line: str) -> str | None:
    """The machine state of an init record, None if the line has none."""
    try:
        rec = json.loads(line[len("ninelives-init "):])
    except ValueError:
        return None
    return rec.get("machine") if isinstance(rec, dict) else None


# What PID 1 must report about itself after the privilege drop (init.md,
# "Privilege"; init/src/privilege.rs): the id and capability sets as
# /proc/1/status prints them, plus the securebits, which it does not show.
INIT_ID = 900
KEPT_CAPS = "0000000000400020"  # CAP_KILL (5) and CAP_SYS_BOOT (22)
SECUREBITS = 0xCF  # NOROOT, NO_SETUID_FIXUP and NO_CAP_AMBIENT_RAISE, each locked
DROP_EXPECTED = {
    "uid": [INIT_ID] * 3,
    "gid": [INIT_ID] * 3,
    "groups": 0,
    "cap_prm": KEPT_CAPS,
    "cap_eff": "0" * 16,
    "cap_inh": "0" * 16,
    "cap_amb": "0" * 16,
    "cap_bnd": KEPT_CAPS,
    "securebits": SECUREBITS,
    "no_new_privs": 1,
    "dumpable": 0,
    "fsuid": INIT_ID,
    "fsgid": INIT_ID,
}


def drop_errors(log: str) -> list[str]:
    """Why the init's drop record does not show an unprivileged PID 1; empty if it does."""
    recs = []
    for l in lines(log):
        if l.startswith("ninelives-init {") and '"step":"drop root"' in l:
            try:
                recs.append(json.loads(l[len("ninelives-init "):]))
            except ValueError:
                return [f"the init's drop record does not parse: {l}"]
    if not recs:
        return ["no drop record: the init never reported dropping root"]
    errors = []
    for key, want in DROP_EXPECTED.items():
        got = recs[-1].get(key)
        if got != want:
            errors.append(f"after the drop, {key} is {got!r}, not {want!r}")
    return errors


def failures(log: str) -> list[tuple[str, str]]:
    """(kind, line) for every console line that is a failure, in log order."""
    out = []
    up = False
    for l in lines(log):
        if l.startswith("ninelives-init {"):
            # The init's own records are not kernel output, except its panic hook's
            # and a machine state other than up once it has been up.
            if '"panic"' in l:
                out.append(("init panic", l))
            elif READY.match(l):
                up = True
            elif up and (state := record_state(l)) not in (None, "up"):
                out.append(("init state", l))
            continue
        for kind, rx in FAILURES:
            if rx.search(l):
                out.append((kind, l))
                break
    return out


def verdict(log: str, expect: str) -> list[str]:
    """Why the boot did not meet the expectation; empty if it did.

    expect is "ready" or "verity".
    """
    bad, ready = failures(log), ready_line(log)
    if expect == "ready":
        errors = []
        if ready is None:
            errors.append("the init's ready line never appeared")
        errors += [f"{kind} on the console: {line}" for kind, line in bad]
        if ready is not None:
            errors += drop_errors(log)
        return errors
    errors = []
    if ready is not None:
        errors.append("the init reported ready on a corrupted image")
    # Only dm-verity's own panic is the expected end; any other failure is a different problem.
    errors += [f"{kind} on the console: {line}" for kind, line in bad
               if not (kind == "verity" or (kind == "panic" and VERITY_PANIC.match(line)))]
    if not any(VERITY_BLOCK.search(l) for l in lines(log)):
        errors.append("no dm-verity corrupted-block line on the console")
    if not any(VERITY_PANIC.match(l) for l in lines(log)):
        errors.append("no 'Kernel panic - not syncing: dm-verity device corrupted' line on the console")
    return errors


def pick_accel(requested: str, kvm_ok: bool) -> str:
    if requested == "auto":
        return "kvm" if kvm_ok else "tcg"
    if requested == "kvm" and not kvm_ok:
        raise SystemExit("--accel kvm, but /dev/kvm is not usable")
    return requested


def qemu_command(qemu: str, accel: str, image: pathlib.Path, code: pathlib.Path, vars_: pathlib.Path,
                 log: pathlib.Path, snapshot: bool, arch: str = "x86_64") -> list[str]:
    machine, cpu_kvm, cpu_tcg = MACHINES[arch]
    return [
        qemu, "-machine", f"{machine},accel={accel}", "-cpu", cpu_kvm if accel == "kvm" else cpu_tcg,
        "-smp", "2", "-m", "1024", "-nodefaults", "-display", "none", "-monitor", "none",
        # A reboot ends the run: a panic's reboot is the signal, and a loop is not wanted.
        "-no-reboot",
        "-serial", f"file:{log}",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_}",
        "-drive", f"file={image},if=none,id=disk0,format=raw" + (",snapshot=on" if snapshot else ""),
        "-device", "virtio-blk-pci,drive=disk0",
    ]


def flip_plan(parts: list[dict], which: str) -> tuple[int, str]:
    """(absolute byte offset in the disk, partition name) of the byte to flip."""
    name, inside = FLIP[which]
    by_name = {p["name"]: p for p in parts}
    if name not in by_name:
        raise ValueError(f"the image has no partition {name}")
    part = by_name[name]
    if part["size"] < BLOCK:
        raise ValueError(f"{name} is smaller than one block")
    return part["offset"] + inside, name


def corrupt_copy(image: pathlib.Path, dest: pathlib.Path, which: str) -> str:
    offset, name = flip_plan(check_image.read_gpt(image), which)
    if subprocess.run(["cp", "--sparse=always", str(image), str(dest)], capture_output=True).returncode != 0:
        shutil.copyfile(image, dest)
    with dest.open("r+b") as f:
        f.seek(offset)
        b = f.read(1)
        f.seek(offset)
        f.write(bytes([b[0] ^ 0xFF]))
    return f"flipped one byte at {offset} in {name}"


def read_log(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def watch(proc: subprocess.Popen, log: pathlib.Path, expect: str, timeout: float) -> tuple[str, float]:
    """Poll the serial log until the boot has its answer or the time is up; returns (why it stopped, seconds)."""
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        text = read_log(log)
        if proc.poll() is not None:
            # -no-reboot: a guest reset ends qemu, so this is the reboot after the panic.
            reset = expect == "verity" and any(VERITY_PANIC.match(l) for l in lines(text))
            if reset:
                return "rebooted", elapsed
            return ("exited after ready" if ready_line(text) else "qemu exited"), elapsed
        if elapsed > timeout:
            return "timeout", elapsed
        if expect == "ready" and (ready_line(text) or failures(text)):
            if ready_line(text):
                time.sleep(SETTLE)
                if proc.poll() is not None:
                    return "exited after ready", elapsed
                return "ready", elapsed
            return "failure", elapsed
        if any(k == "firmware" for k, _ in failures(text)):
            return "failure", elapsed
        if expect == "verity" and ready_line(text):
            return "ready", elapsed
        if expect == "verity" and any(k in ("verity", "panic") for k, _ in failures(text)):
            # The panic's reboot ends qemu (-no-reboot); give it time to.
            try:
                proc.wait(REBOOT_WAIT)
                return "rebooted", time.monotonic() - start
            except subprocess.TimeoutExpired:
                return "no reboot", time.monotonic() - start
        time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=pathlib.Path, required=True)
    ap.add_argument("--ovmf-code", type=pathlib.Path, required=True)
    ap.add_argument("--ovmf-vars", type=pathlib.Path, required=True, help="the firmware's variable store template; copied, never written")
    ap.add_argument("--log", type=pathlib.Path, required=True, help="where the serial console goes")
    ap.add_argument("--corrupt", choices=sorted(FLIP), help="flip one byte in the root or the hash tree of a copy first")
    ap.add_argument("--accel", choices=["auto", "kvm", "tcg"], default="auto")
    ap.add_argument("--timeout", type=float, help="seconds; default by accelerator")
    ap.add_argument("--arch", choices=sorted(MACHINES), default="x86_64")
    ap.add_argument("--qemu", help="default: qemu-system-ARCH")
    args = ap.parse_args()

    kvm_ok = os.access("/dev/kvm", os.R_OK | os.W_OK)
    accel = pick_accel(args.accel, kvm_ok)
    timeout = args.timeout or TIMEOUTS[args.arch][accel]
    expect = "verity" if args.corrupt else "ready"
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_bytes(b"")
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d)
        vars_copy = work / "vars.fd"
        shutil.copyfile(args.ovmf_vars, vars_copy)
        image, note = args.image, ""
        if args.corrupt:
            image = work / "corrupt.raw"
            note = corrupt_copy(args.image, image, args.corrupt)
            print(note)
        cmd = qemu_command(args.qemu or QEMU[args.arch], accel, image, args.ovmf_code, vars_copy, args.log,
                           snapshot=not args.corrupt, arch=args.arch)
        print("+", " ".join(cmd), flush=True)
        with (work / "qemu.stderr").open("wb") as err:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=err, stderr=err)
            try:
                why, secs = watch(proc, args.log, expect, timeout)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
        stderr = (work / "qemu.stderr").read_text(errors="replace").strip()

    text = read_log(args.log)
    errors = verdict(text, expect)
    if why in ("timeout", "no reboot", "exited after ready") or (why == "qemu exited" and expect == "ready"):
        errors.append({"timeout": f"no answer within {timeout:.0f}s", "no reboot": "the panic did not reboot the VM",
                       "exited after ready": "qemu exited after the ready line"}.get(why, "qemu exited before the init was ready"))
    if expect == "verity" and why not in ("rebooted", "no reboot", "timeout"):
        errors.append(f"the VM did not reset after the panic (stopped: {why})")
    if stderr:
        print("qemu stderr:\n" + stderr)
    result = {"expect": expect, "arch": args.arch, "accel": accel, "stopped": why, "seconds": round(secs, 1), "corruption": note,
              "ok": not errors, "errors": errors}
    print("boot-result: " + json.dumps(result))
    if errors:
        print("---- serial console, last 80 lines ----")
        print("\n".join(lines(text)[-80:]))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
