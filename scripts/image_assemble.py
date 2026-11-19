#!/usr/bin/env python3
"""Seal a staged root into the image outputs (docs/components/image.md, "Building the root", step 6).

  root.erofs      mkfs.erofs: uncompressed, no extended attributes, fixed time and UUID
  root.verity     veritysetup: hash tree, salt derived from the commit
  roothash        the tree's root hash
  efi             the unified kernel image: kernel, command line, os-release
  raw             systemd-repart: the GPT disk with the design's partition table

  image_assemble.py --root DIR --kernel FILE --kernel-version V --commit SHA \
      --source-date-epoch N --target images/qemu-x86_64/target.toml --out DIR --name NAME
"""
import argparse
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_kernel_config as kc  # noqa: E402
import image_common as ic  # noqa: E402

BLOCK = 4096
MIB = 1 << 20
# No Discoverable Partitions Specification type exists for the config blob.
CONFIG_TYPE = "9b0f2a6e-5d3c-4c0e-8d6a-7a1e5f4c2b90"
STUB = "/usr/lib/systemd/boot/efi/linuxx64.efi.stub"
SBAT = (
    "sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md\n"
    "ninelives,1,NineLives,ninelives,1,https://github.com/GetFelix/ninelives\n"
)


def derived_uuid(seed: str, *parts: str) -> str:
    return str(uuid.uuid5(uuid.UUID(seed), "/".join(parts)))


def verity_salt(commit: str) -> str:
    return hashlib.sha256(b"ninelives-verity-salt:" + commit.encode()).hexdigest()


def cmdline(target: dict, policy: "kc.Policy", sectors: int, blocks: int, roothash: str, salt: str) -> str:
    """The one command line for the target; check_image.py rebuilds it and compares."""
    table = (f"root,,,ro,0 {sectors} verity 1 {target['root_device']} {target['verity_device']} "
             f"{BLOCK} {BLOCK} {blocks} 0 sha256 {roothash} {salt} 1 panic_on_corruption")
    words = [
        "init=/usr/lib/ninelives/init",
        f"console={target['console']}",
        "ro",
        "rootfstype=erofs",
        "root=/dev/dm-0",
        f"dm-mod.waitfor={target['root_device']},{target['verity_device']}",
        f'dm-mod.create="{table}"',
    ]
    words += [w for w in policy.required if w != "ro"]
    return " ".join(words)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, check=True, text=True, **kw)


def round_up(n: int, to: int) -> int:
    return -(-n // to) * to


def repart_definitions(d: pathlib.Path, target: dict, root_size: int, verity_size: int, esp_dir: pathlib.Path,
                       erofs: pathlib.Path, verity: pathlib.Path) -> None:
    sizes = target["sizes"]
    root_size, verity_size = round_up(root_size, MIB), round_up(verity_size, MIB)

    def conf(name: str, **kv) -> None:
        text = "[Partition]\n" + "".join(f"{k}={v}\n" for k, v in kv.items())
        (d / name).write_text(text, encoding="utf-8")

    fixed = lambda n: {"SizeMinBytes": n, "SizeMaxBytes": n}  # noqa: E731
    conf("00-esp.conf", Type="esp", Label="esp", Format="vfat", CopyFiles=f"{esp_dir}:/",
         **fixed(sizes["esp_mib"] * MIB))
    conf("10-root-a.conf", Type="root-x86-64", Label="ninelives-root-a", CopyBlocks=erofs, ReadOnly="yes", **fixed(root_size))
    conf("20-verity-a.conf", Type="root-x86-64-verity", Label="ninelives-verity-a", CopyBlocks=verity, ReadOnly="yes", **fixed(verity_size))
    conf("30-root-b.conf", Type="root-x86-64", Label="ninelives-root-b", **fixed(root_size))
    conf("40-verity-b.conf", Type="root-x86-64-verity", Label="ninelives-verity-b", **fixed(verity_size))
    conf("50-config.conf", Type=CONFIG_TYPE, Label="ninelives-config", **fixed(sizes["config_mib"] * MIB))
    conf("60-data.conf", Type="linux-generic", Label="ninelives-data", SizeMinBytes=sizes["data_mib"] * MIB)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=pathlib.Path, required=True)
    ap.add_argument("--kernel", type=pathlib.Path, required=True)
    ap.add_argument("--kernel-version", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--source-date-epoch", type=int, required=True)
    ap.add_argument("--target", type=pathlib.Path, default=ic.IMAGES / "qemu-x86_64" / "target.toml")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--name", required=True)
    args = ap.parse_args()

    target = ic.load_toml(args.target)
    arch = target["architecture"]
    out, name = args.out, args.name
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(args.source_date_epoch)}

    erofs, verity = out / f"{name}.root.erofs", out / f"{name}.root.verity"
    run(["mkfs.erofs", f"-b{BLOCK}", "-x-1", f"-T{args.source_date_epoch}", "--all-root", "--hard-dereference", "--quiet",
         f"-U{derived_uuid(target['erofs_uuid_seed'], arch, 'root')}", str(erofs), str(args.root)], env=env)
    size = erofs.stat().st_size
    if size % BLOCK:
        raise SystemExit(f"{erofs.name}: {size} bytes is not a whole number of {BLOCK}-byte blocks")

    salt = verity_salt(args.commit)
    r = run(["veritysetup", "format", "--no-superblock", "--hash=sha256", f"--data-block-size={BLOCK}",
             f"--hash-block-size={BLOCK}", f"--salt={salt}", str(erofs), str(verity)], capture_output=True)
    m = re.search(r"Root hash:\s*([0-9a-f]{64})", r.stdout)
    if not m:
        raise SystemExit(f"veritysetup printed no root hash: {r.stdout!r}")
    roothash = m.group(1)
    (out / f"{name}.roothash").write_text(roothash + "\n", encoding="ascii")

    policy = kc.load_policy(kc.KERNEL / "policy.toml", arch)
    line = cmdline(target, policy, size // 512, size // BLOCK, roothash, salt)
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    (work / "esp" / "EFI" / "BOOT").mkdir(parents=True)
    (work / "cmdline").write_text(line, encoding="ascii")
    (work / "os-release").write_text(
        'NAME="NineLives"\nID=ninelives\nPRETTY_NAME="NineLives"\n'
        f'VARIANT_ID=production\nBUILD_ID={args.commit[:12]}\n', encoding="utf-8")
    (work / "sbat").write_text(SBAT, encoding="ascii")
    uki = out / f"{name}.efi"
    run(["ukify", "build", f"--linux={args.kernel}", f"--cmdline=@{work / 'cmdline'}", f"--os-release=@{work / 'os-release'}",
         f"--sbat=@{work / 'sbat'}", f"--uname={args.kernel_version}", f"--stub={STUB}", f"--output={uki}"], env=env)
    esp_uki = work / "esp" / "EFI" / "BOOT" / "BOOTX64.EFI"
    shutil.copyfile(uki, esp_uki)
    for p in (esp_uki, esp_uki.parent, esp_uki.parent.parent):
        os.utime(p, (args.source_date_epoch, args.source_date_epoch))

    defs = work / "repart.d"
    defs.mkdir()
    repart_definitions(defs, target, size, verity.stat().st_size, work / "esp", erofs, verity)
    raw = out / f"{name}.raw"
    raw.unlink(missing_ok=True)
    run(["systemd-repart", "--empty=create", "--size=auto", f"--definitions={defs}",
         f"--seed={derived_uuid(target['disk_seed'], arch, 'disk')}", "--sector-size=512", "--offline=yes", "--dry-run=no", "--pretty=no", str(raw)], env=env)
    shutil.rmtree(work)
    print(f"sealed {name}: root {size} bytes, root hash {roothash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
