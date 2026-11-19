#!/usr/bin/env python3
"""Check a built image against docs/components/image.md, "Checks".

Every check reads what boots: the erofs partition and the unified kernel
image are cut out of the finished disk, the way firmware and the kernel will
read them, never taken from the staging directory they were made from. Each
check returns every violation it finds, so one run shows the whole list.

  check_image.py --image X.raw --meta DIR --out DIR [--propose-executables]

--meta holds origins.json and stage.json from image_stage.py. Writes
X.files.json next to the other outputs. Needs root (mount) and the build
tools (veritysetup, mcopy).
"""
import argparse
import contextlib
import json
import os
import pathlib
import posixpath
import re
import shlex
import stat
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_kernel_config as kc  # noqa: E402
import image_assemble as asm  # noqa: E402
import image_common as ic  # noqa: E402

# Shells and interpreters by name, with version suffixes (python3.13, perl5.40.1, bash-static).
BANNED_NAME = re.compile(r"^(a?sh|bash|dash|zsh|t?csh|ksh\d*|fish|busybox|perl[\d.]*|python[\d.]*|g?awk|mawk|nawk|lua[\d.]*|"
                         r"tclsh[\d.]*|wish[\d.]*|ruby[\d.]*|node|nodejs|php[\d.]*|pwsh)(-.*)?$")
BANNED_PACKAGES = {"apt", "dpkg", "aptitude", "gcc", "g++", "clang", "make", "openssh-server", "openssh-client", "bash", "dash",
                   "perl", "perl-base", "python3", "sudo", "login", "passwd"}
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY"),
    re.compile(rb"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
]
GPT_PARTITIONS = [  # name, type GUID
    ("esp", "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"),
    ("ninelives-root-a", "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"),
    ("ninelives-verity-a", "2c7357ed-ebd2-46d9-aec1-23d437ec2bf5"),
    ("ninelives-root-b", "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"),
    ("ninelives-verity-b", "2c7357ed-ebd2-46d9-aec1-23d437ec2bf5"),
    ("ninelives-config", asm.CONFIG_TYPE),
    ("ninelives-data", "0fc63daf-8483-4772-8e79-3d69d8477de4"),
]


class Entry:
    def __init__(self, path, kind, mode=0o444, uid=0, gid=0, size=0, xattrs=(), link="", content=None, reader=None, mtime=None):
        self.path, self.kind, self.mode, self.uid, self.gid, self.mtime = path, kind, mode, uid, gid, mtime
        self.size, self.xattrs, self.link = size, list(xattrs), link
        self._content, self._reader = content, reader

    def content(self) -> bytes:
        if self._content is None:
            self._content = self._reader(self.path) if self._reader else b""
        return self._content

    def sha256(self) -> str:
        import hashlib
        return hashlib.sha256(self.content()).hexdigest()


KINDS = {stat.S_IFREG: "file", stat.S_IFDIR: "dir", stat.S_IFLNK: "symlink", stat.S_IFIFO: "fifo",
         stat.S_IFSOCK: "socket", stat.S_IFCHR: "chr", stat.S_IFBLK: "blk"}


def walk_tree(root: pathlib.Path) -> list[Entry]:
    def reader(path: str) -> bytes:
        return (root / path.lstrip("/")).read_bytes()

    out = [Entry("/", "dir", stat.S_IMODE(os.lstat(root).st_mode), os.lstat(root).st_uid, os.lstat(root).st_gid)]
    for dirpath, dirnames, filenames in os.walk(root):
        for n in sorted(dirnames + filenames):
            p = pathlib.Path(dirpath) / n
            st = os.lstat(p)
            kind = KINDS.get(stat.S_IFMT(st.st_mode), "other")
            try:
                xattrs = os.listxattr(p, follow_symlinks=False)
            except OSError:
                xattrs = []
            out.append(Entry("/" + os.path.relpath(p, root), kind, stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, st.st_size,
                             xattrs, os.readlink(p) if kind == "symlink" else "", reader=reader if kind == "file" else None,
                             mtime=int(st.st_mtime)))
    return sorted(out, key=lambda e: e.path)


# ---------------------------------------------------------------------------
# The checks. Each takes entries (and config) and returns violation strings.


def check_setuid(entries):
    return [f"setuid/setgid: {e.path} mode {e.mode:o}" for e in entries if e.mode & (stat.S_ISUID | stat.S_ISGID)]


def check_times(entries, sde: int):
    return [f"timestamp: {e.path} is {e.mtime}, not SOURCE_DATE_EPOCH {sde}" for e in entries if e.mtime is not None and e.mtime != sde]


def check_xattrs(entries):
    return [f"extended attribute: {e.path} has {', '.join(e.xattrs)}" for e in entries if e.xattrs]


def check_ownership(entries):
    out = []
    for e in entries:
        if (e.uid, e.gid) != (0, 0):
            out.append(f"owner: {e.path} is {e.uid}:{e.gid}, not 0:0")
        if e.kind != "symlink" and e.mode & 0o222:
            out.append(f"write bit: {e.path} mode {e.mode:o}")
        if e.kind != "symlink" and e.mode & 0o002:
            out.append(f"world-writable: {e.path}")
    return out


def glob_match(path: str, pattern: str) -> bool:
    """* matches within one path segment, never across a /."""
    rx = "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, path) is not None


def check_special(entries, allowed_links: dict[str, str]):
    out = []
    paths = {e.path for e in entries}
    for e in entries:
        if e.kind not in ("file", "dir", "symlink"):
            out.append(f"special file: {e.path} is a {e.kind}")
        elif e.kind == "symlink":
            target = e.link if e.link.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(e.path), e.link))
            if allowed_links.get(e.path) == e.link:
                continue
            if target not in paths:
                out.append(f"symlink: {e.path} -> {e.link} leaves the root and is not in images/symlinks.toml")
            elif target.startswith(("/run", "/tmp", "/data", "/proc", "/sys", "/dev")):
                out.append(f"symlink: {e.path} -> {e.link} points into writable storage")
    return out


def check_users(entries, users: dict):
    by = {e.path: e for e in entries}
    out = []
    want_passwd = "".join(f"{u['name']}:x:{u['uid']}:{u['gid']}:{u['name']}:{u['home']}:{u['shell']}\n" for u in users["user"])
    want_group = "".join(f"{g['name']}:x:{g['gid']}:\n" for g in users["group"])
    for path, want in (("/etc/passwd", want_passwd), ("/etc/group", want_group)):
        e = by.get(path)
        if e is None or e.content().decode("utf-8", "replace") != want:
            out.append(f"users: {path} is not what images/users.toml says")
    for path in ("/etc/shadow", "/etc/gshadow"):
        e = by.get(path)
        if e is not None:
            for line in e.content().decode().splitlines():
                if line.split(":")[1:2] not in (["*"], ["!"], ["!*"], ["x"]) and not line.split(":")[1].startswith("!"):
                    out.append(f"users: {path} has an unlocked entry")
    uid0 = [u for u in users["user"] if u["uid"] == 0]
    if len(uid0) != 1 or uid0[0]["name"] != "root":
        out.append("users: users.toml must have exactly one uid 0 entry, root")
    for u in users["user"]:
        if u["shell"] != "/usr/sbin/nologin":
            out.append(f"users: {u['name']} has shell {u['shell']}")
        if u["home"] in by:
            out.append(f"users: {u['name']}'s home {u['home']} exists")
    return out


def is_exec_candidate(e: Entry) -> bool:
    if e.kind != "file":
        return False
    return bool(e.mode & 0o111) or e.content()[:4] == b"\x7fELF"


def check_executables(entries, allowlist: list[dict]):
    out = []
    for item in allowlist:
        if BANNED_NAME.match(posixpath.basename(item["path"])):
            out.append(f"executables.toml: {item['path']} is a shell or interpreter")
    used = [0] * len(allowlist)
    for e in entries:
        if e.kind != "file":
            continue
        if e.content()[:2] == b"#!":
            out.append(f"script: {e.path} starts with #!")
        if not is_exec_candidate(e):
            continue
        # Every file that is actually there, whatever pattern lets it in.
        if BANNED_NAME.match(posixpath.basename(e.path)):
            out.append(f"shell or interpreter: {e.path}")
        hits = [i for i, item in enumerate(allowlist) if glob_match(e.path, item["path"])]
        for i in hits:
            used[i] += 1
        if not hits:
            out.append(f"executable not in images/executables.toml: {e.path}")
    for i, item in enumerate(allowlist):
        if not used[i]:
            out.append(f"executables.toml lists {item['path']}, which is not in the image")
    return out


def check_elf(entries):
    out = []
    for e in entries:
        if e.kind == "file" and e.content()[:4] == b"\x7fELF":
            try:
                elf = ic.Elf(e.content())
            except ValueError as ex:
                out.append(f"ELF: {e.path}: {ex}")
                continue
            if elf.executable_stack:
                out.append(f"ELF: {e.path} has an executable stack")
            if elf.writable_executable:
                out.append(f"ELF: {e.path} has a segment that is writable and executable")
    return out


def check_needed(entries, allowlist: list[dict]):
    out = []
    for item in allowlist:
        if "needed" not in item:
            continue
        for e in entries:
            if e.kind == "file" and glob_match(e.path, item["path"]):
                got = sorted(ic.Elf(e.content()).needed)
                if got != sorted(item["needed"]):
                    out.append(f"DT_NEEDED: {e.path} needs {got}, images/executables.toml says {sorted(item['needed'])}")
    return out


def check_needed_resolve(entries):
    """Every shared library an ELF names is somewhere in the root."""
    libs = {posixpath.basename(e.path) for e in entries if e.kind in ("file", "symlink")}
    out = []
    for e in entries:
        if e.kind == "file" and e.content()[:4] == b"\x7fELF":
            for n in ic.Elf(e.content()).needed:
                if n not in libs:
                    out.append(f"DT_NEEDED: {e.path} needs {n}, which is not in the root")
    return out


def check_packages(stage: dict, keep: list[dict], removals: dict[str, str], lock: dict):
    out = ic.package_set_errors({p["name"] for p in stage["installed"]}, {p["name"] for p in keep}, removals)
    locked = {p["name"]: p for p in lock["root"]["package"]}
    for p in stage["installed"]:
        l = locked.get(p["name"])
        if l is None or (l["version"], l["sha256"]) != (p["version"], p["sha256"]):
            out.append(f"package: {p['name']} does not match debian.lock")
        if p["name"] in BANNED_PACKAGES and p["kept"]:
            out.append(f"package: {p['name']} is a package manager, compiler, shell or interpreter")
    if {p["name"] for p in stage["installed"] if p["kept"]} != {p["name"] for p in keep}:
        out.append("package: the kept packages are not packages.toml")
    return out


def check_origins(entries, origins: dict):
    out = []
    dirs_with_children = {posixpath.dirname(e.path) for e in entries}
    for e in entries:
        if e.path == "/":
            continue
        o = origins.get(e.path)
        if o is None:
            if e.kind == "dir" and e.path in dirs_with_children:
                # A directory is explained by what is inside it.
                continue
            out.append(f"origin: {e.path} has none")
            continue
        if o["type"] != e.kind:
            out.append(f"origin: {e.path} is a {e.kind}, {o['package']} says {o['type']}")
        elif e.kind == "file" and e.sha256() != o["sha256"]:
            out.append(f"origin: {e.path} differs from {o['package']}'s member")
        elif e.kind == "symlink" and e.link != o["link"]:
            out.append(f"origin: {e.path} points to {e.link}, {o['package']} says {o['link']}")
    return out


def check_secrets(entries):
    out = []
    for e in entries:
        if e.kind == "file":
            data = e.content()
            for pat in SECRET_PATTERNS:
                if pat.search(data):
                    out.append(f"secret: {e.path} matches {pat.pattern[:30]!r}")
    return out


def check_cmdline(line: str, expected: str, policy) -> list[str]:
    out = []
    if line != expected:
        out.append(f"cmdline: the UKI's line is not the expected one\n  have {line}\n  want {expected}")
    out += [f"cmdline policy: {e}" for e in kc.check_cmdline(line, policy)]
    return out


def check_esp(files: list[str], uki: bytes, expected_uki: bytes):
    out = []
    want = {"EFI", "EFI/BOOT", "EFI/BOOT/BOOTX64.EFI"}
    got = {f.strip("/") for f in files}
    if {g.upper() for g in got} != {w.upper() for w in want}:
        out.append(f"ESP: holds {sorted(got)}, expected exactly {sorted(want)}")
    if uki != expected_uki:
        out.append("ESP: BOOTX64.EFI is not the built UKI")
    return out


def check_budget(sizes: dict[str, int], budget: dict):
    out = []
    for k, limit in budget.items():
        v = sizes.get(k)
        if v is None:
            out.append(f"budget: no size measured for {k}")
        elif limit and v > limit:
            out.append(f"budget: {k} is {v} bytes, over the limit {limit}")
        elif limit and v > limit * 0.9:
            print(f"warning: {k} is at {100 * v // limit}% of its budget", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Reading the finished disk


def read_gpt(path: pathlib.Path) -> list[dict]:
    with path.open("rb") as f:
        f.seek(512)
        hdr = f.read(92)
        if hdr[:8] != b"EFI PART":
            raise ValueError("no GPT header")
        entries_lba, n, size = struct.unpack_from("<QII", hdr, 72)
        f.seek(entries_lba * 512)
        raw = f.read(n * size)
    import uuid
    out = []
    for i in range(n):
        e = raw[i * size:(i + 1) * size]
        if e[:16] == b"\0" * 16:
            continue
        first, last = struct.unpack_from("<QQ", e, 32)
        out.append({"type": str(uuid.UUID(bytes_le=e[:16])), "uuid": str(uuid.UUID(bytes_le=e[16:32])),
                    "name": e[56:128].decode("utf-16-le").rstrip("\0"), "offset": first * 512, "size": (last - first + 1) * 512})
    return out


def config_bytes(path: pathlib.Path, part: dict) -> bytes:
    with path.open("rb") as f:
        f.seek(part["offset"])
        return f.read(part["size"])


def cut(path: pathlib.Path, part: dict, dest: pathlib.Path, limit: int | None = None) -> None:
    with path.open("rb") as f, dest.open("wb") as o:
        f.seek(part["offset"])
        left = part["size"] if limit is None else min(limit, part["size"])
        while left:
            chunk = f.read(min(left, 1 << 24))
            o.write(chunk)
            left -= len(chunk)


def check_gpt(parts: list[dict], sizes: dict, config_is_zero: bool):
    """Names, types and the sizes the design fixes: ESP 256 MiB, config 1 MiB, A and B slots alike."""
    names = [p["name"] for p in parts]
    want = [n for n, _ in GPT_PARTITIONS]
    if names != want:
        return [f"GPT: partitions are {names}, expected {want}"]
    out = []
    for p, (n, t) in zip(parts, GPT_PARTITIONS):
        if p["type"] != t:
            out.append(f"GPT: {n} has type {p['type']}, expected {t}")
    by = {p["name"]: p for p in parts}
    for name, mib in (("esp", sizes["esp_mib"]), ("ninelives-config", sizes["config_mib"])):
        if by[name]["size"] != mib * (1 << 20):
            out.append(f"GPT: {name} is {by[name]['size']} bytes, the design fixes {mib} MiB")
    if by["ninelives-root-a"]["size"] != by["ninelives-root-b"]["size"] or by["ninelives-verity-a"]["size"] != by["ninelives-verity-b"]["size"]:
        out.append("GPT: the B slot is not the size of the A slot")
    if by["ninelives-data"]["size"] < sizes["data_mib"] * (1 << 20):
        out.append("GPT: the data partition is smaller than the image's minimum")
    if not config_is_zero:
        out.append("GPT: the config partition is not all zeros")
    return out


def pe_sections(data: bytes) -> dict[str, bytes]:
    if data[:2] != b"MZ":
        raise ValueError("not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("no PE header")
    nsec, = struct.unpack_from("<H", data, pe + 6)
    optsize, = struct.unpack_from("<H", data, pe + 20)
    out = {}
    table = pe + 24 + optsize
    for i in range(nsec):
        s = data[table + i * 40:table + (i + 1) * 40]
        name = s[:8].rstrip(b"\0").decode("ascii")
        vsize, _, rawsize, rawptr = struct.unpack_from("<IIII", s, 8)
        out[name] = data[rawptr:rawptr + min(vsize, rawsize)] if vsize else data[rawptr:rawptr + rawsize]
    return out


UKI_REQUIRED = {".linux", ".cmdline", ".osrel", ".sbat"}
# What the systemd stub itself carries, plus .uname which ukify adds from --uname.
UKI_STUB = {".text", ".data", ".rdata", ".rodata", ".reloc", ".pdata", ".sdmagic", ".uname"}


def check_uki_sections(sections: dict[str, bytes], kernel_sha256: str):
    """Exactly the sections the build puts in, and the kernel is the one the kernel job built."""
    import hashlib

    out = []
    for name in sorted(UKI_REQUIRED - set(sections)):
        out.append(f"UKI: missing section {name}")
    for name in sorted(set(sections) - UKI_REQUIRED - UKI_STUB):
        out.append(f"UKI: unexpected section {name} (no initrd, microcode, devicetree or profile is allowed)")
    if ".linux" in sections and hashlib.sha256(sections[".linux"]).hexdigest() != kernel_sha256:
        out.append("UKI: .linux is not the kernel artifact of this run")
    return out


def dm_create(line: str) -> dict:
    """Pull the verity parameters out of the dm-mod.create word."""
    words = shlex.split(line)
    create = next(w for w in words if w.startswith("dm-mod.create="))[len("dm-mod.create="):]
    f = create.split(",", 4)[4].split()
    # 0 <sectors> verity 1 <data> <hash> <bs> <bs> <blocks> <start> sha256 <roothash> <salt> ...
    return {"sectors": int(f[1]), "blocks": int(f[8]), "roothash": f[11], "salt": f[12]}


@contextlib.contextmanager
def mounted(image: pathlib.Path):
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["mount", "-t", "erofs", "-o", "ro,loop", str(image), d], check=True)
        try:
            yield pathlib.Path(d)
        finally:
            subprocess.run(["umount", d], check=True)


def propose_executables(entries) -> str:
    groups: dict[str, list[str]] = {}
    for e in entries:
        if e.kind == "file" and is_exec_candidate(e):
            groups.setdefault(posixpath.dirname(e.path), []).append(e.path)
    lines = ["# Proposed from the built image. Review each entry, then add origin and requirement.", ""]
    for d, paths in sorted(groups.items()):
        exts = {posixpath.splitext(p)[1] for p in paths}
        if len(paths) > 8 and len(exts) == 1:
            patterns = [f"{d}/*{exts.pop()}"]
        else:
            patterns = sorted(paths)
        for p in patterns:
            lines += ["[[executable]]", f'path = "{p}"', 'origin = "TODO"', 'requirement = "S1"', ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=pathlib.Path, required=True)
    ap.add_argument("--meta", type=pathlib.Path, required=True)
    ap.add_argument("--target", type=pathlib.Path, default=ic.IMAGES / "qemu-x86_64" / "target.toml")
    ap.add_argument("--commit", required=True)
    ap.add_argument("--kernel", type=pathlib.Path, required=True, help="the kernel artifact directory")
    ap.add_argument("--source-date-epoch", type=int, required=True)
    ap.add_argument("--propose-executables", action="store_true")
    args = ap.parse_args()

    base = str(args.image)[:-len(".raw")]
    target = ic.load_toml(args.target)
    arch = target["architecture"]
    origins = json.loads((args.meta / "origins.json").read_text())
    stage = json.loads((args.meta / "stage.json").read_text())
    lock = ic.load_toml(ic.IMAGES / "debian.lock")
    keep = ic.load_toml(ic.IMAGES / "packages.toml")["package"]
    users = ic.load_toml(ic.IMAGES / "users.toml")
    removals = ic.load_removals(ic.ROOT / "rootfs" / "removals.toml")
    execs = ic.load_toml(ic.IMAGES / "executables.toml").get("executable", []) if (ic.IMAGES / "executables.toml").exists() else []
    links = {l["path"]: l["target"] for l in (ic.load_toml(ic.IMAGES / "symlinks.toml").get("symlink", [])
                                              if (ic.IMAGES / "symlinks.toml").exists() else [])}
    budget = ic.load_toml(ic.IMAGES / "budget.toml")[arch]

    errors: list[str] = []
    parts = read_gpt(args.image)
    sizes_cfg = target["sizes"]
    by_name = {p["name"]: p for p in parts}
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        erofs, verity, esp = d / "root.erofs", d / "root.verity", d / "esp.img"
        uki_path = pathlib.Path(base + ".efi")
        errors += check_gpt(parts, sizes_cfg, set(config_bytes(args.image, by_name["ninelives-config"])) <= {0})
        sections = pe_sections(uki_path.read_bytes())
        errors += check_uki_sections(sections, ic.sha256_file(args.kernel / "bzImage"))
        line = sections[".cmdline"].rstrip(b"\0").decode("ascii")
        dm = dm_create(line)
        sizes = dm["sectors"] * 512
        # The partition is padded to a MiB; the table covers the file's own length.
        cut(args.image, by_name["ninelives-root-a"], erofs, sizes)
        cut(args.image, by_name["ninelives-verity-a"], verity)
        cut(args.image, by_name["esp"], esp)
        if ic.sha256_file(erofs) != ic.sha256_file(pathlib.Path(base + ".root.erofs")):
            errors.append("disk: the root partition is not the built root.erofs")
        if dm["blocks"] * asm.BLOCK != sizes:
            errors.append("cmdline: the verity table's block count does not match its sector count")

        espdir = d / "espfiles"
        espdir.mkdir()
        subprocess.run(["mcopy", "-s", "-n", "-i", str(esp), "::", str(espdir)], check=False)
        esp_files = sorted(os.path.relpath(os.path.join(dp, n), espdir) for dp, dn, fn in os.walk(espdir) for n in dn + fn)
        esp_uki = next((espdir / f for f in esp_files if f.upper() == "EFI/BOOT/BOOTX64.EFI"), None)
        errors += check_esp(esp_files, esp_uki.read_bytes() if esp_uki else b"", uki_path.read_bytes())

        policy = kc.load_policy(kc.KERNEL / "policy.toml", arch)
        expected = asm.cmdline(target, policy, dm["sectors"], dm["blocks"], dm["roothash"], dm["salt"])
        errors += check_cmdline(line, expected, policy)
        if dm["salt"] != asm.verity_salt(args.commit):
            errors.append("cmdline: the verity salt is not derived from this commit")
        v = subprocess.run(["veritysetup", "verify", "--no-superblock", "--hash=sha256", f"--data-block-size={asm.BLOCK}",
                            f"--hash-block-size={asm.BLOCK}", f"--data-blocks={dm['blocks']}", f"--salt={dm['salt']}",
                            str(erofs), str(verity), dm["roothash"]], capture_output=True, text=True)
        if v.returncode != 0:
            errors.append(f"verity: veritysetup verify failed: {v.stdout.strip()} {v.stderr.strip()}")
        os_rel = sections.get(".osrel", b"").decode("utf-8", "replace")
        if "VARIANT_ID=production" not in os_rel:
            errors.append("UKI: os-release does not say VARIANT_ID=production")

        with mounted(erofs) as root:
            entries = walk_tree(root)
            if args.propose_executables:
                print(propose_executables(entries))
                return 0
            errors += check_setuid(entries) + check_times(entries, args.source_date_epoch) + check_xattrs(entries) + check_ownership(entries)
            errors += check_special(entries, links) + check_users(entries, users)
            errors += check_executables(entries, execs) + check_elf(entries) + check_needed(entries, execs) + check_needed_resolve(entries)
            errors += check_packages(stage, keep, removals, lock) + check_origins(entries, origins) + check_secrets(entries)
            files = [{"path": e.path, "type": e.kind, "mode": e.mode, "uid": e.uid, "gid": e.gid, "size": e.size,
                      **({"sha256": e.sha256()} if e.kind == "file" else {}), **({"link": e.link} if e.link else {})}
                     for e in entries]
        raw_size = args.image.stat().st_size
        errors += check_budget({"root_erofs": sizes, "uki": uki_path.stat().st_size,
                                "esp_files": sum(p.stat().st_size for p in espdir.rglob("*") if p.is_file()), "raw": raw_size}, budget)
    pathlib.Path(base + ".files.json").write_text(json.dumps(files, indent=1) + "\n", encoding="utf-8")
    for e in errors:
        print(e, file=sys.stderr)
    print(f"{len(errors)} violations")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
