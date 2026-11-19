#!/usr/bin/env python3
"""Turn mkosi's package tree into the root that gets sealed.

Runs as root on the tree mkosi wrote, offline, after the build's package
install (docs/components/image.md, "Building the root", steps 2 to 5):

  1. remove the packages that are installed but not in images/packages.toml,
     using dpkg's own file lists, then delete dpkg's database
  2. unpack Felix and felixd from the verified archives, refusing anything
     but regular files, and install them 0:0 mode 0555
  3. add the init, /etc/passwd and /etc/group from images/users.toml, and
     os-release with the variant
  4. normalise: owner 0:0, no write bits, no setuid or setgid, no extended
     attributes, timestamps at SOURCE_DATE_EPOCH
  5. write origins.json, which says where every path in the root came from;
     check_image.py holds the finished image to it

  image_stage.py --root DIR --store DIR --init FILE --commit SHA \
      --source-date-epoch N --meta DIR
"""
import argparse
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tarfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import image_common as ic  # noqa: E402
import image_lock  # noqa: E402

# Runtime mount points the init mounts over (init design): present as empty
# directories, 0555 in the image.
MOUNT_POINTS = ["/proc", "/sys", "/dev", "/run", "/tmp", "/data"]
# Left by mkosi and dpkg; the root has no use for any of it.
# Each is left by a maintainer script or by mkosi, and nothing in the root uses it.
RESIDUE = [
    # mkosi and dpkg themselves
    "/var/lib/dpkg", "/var/lib/apt", "/var/cache", "/var/log", "/etc/apt", "/etc/dpkg",
    "/var/lib/systemd", "/etc/machine-id", "/etc/hostname", "/etc/resolv.conf", "/boot/loader",
    "/lib32", "/libx32",
    # update-alternatives, pam, shells, login: for packages that are gone
    "/etc/alternatives", "/etc/pam.d", "/etc/security", "/var/lib/pam", "/etc/shells", "/var/lib/shells.state",
    "/etc/subuid", "/etc/subgid", "/etc/.pwd.lock", "/usr/bin/awk", "/usr/bin/nawk", "/usr/bin/pager",
    "/usr/bin/which", "/usr/sbin/rmt",
    # base-files' shell and login skeleton
    "/etc/profile", "/etc/environment", "/etc/motd", "/etc/update-motd.d", "/root/.bashrc", "/root/.profile",
    # timers and docs from removed packages, and links into /run that nothing writes
    "/etc/systemd", "/usr/share/info", "/usr/local/man", "/usr/lib/clock-epoch", "/var/spool/mail",
]
# Links into /run, the init's tmpfs, that base-files makes in its postinst.
RUN_LINKS = {"/var/run": "/run", "/var/lock": "/run/lock"}
# Name lookups: local files, then DNS. libnss_files and libnss_dns are the two NSS modules the image keeps.
NSSWITCH = "passwd: files\ngroup: files\nhosts: files dns\n"
LD_SO_CONF = "include /etc/ld.so.conf.d/*.conf\n"
TOP_LEVEL_LINKS = {"/bin": "usr/bin", "/sbin": "usr/sbin", "/lib": "usr/lib", "/lib64": "usr/lib64"}
INSTALL_MODE = 0o555


def installed_packages(root: pathlib.Path) -> dict[str, str]:
    """name -> version from dpkg's status file."""
    status = (root / "var/lib/dpkg/status").read_text(encoding="utf-8")
    out = {}
    for st in ic.parse_deb822(status):
        if st.get("Status", "").endswith(" installed"):
            out[st["Package"]] = st["Version"]
    return out


def package_files(root: pathlib.Path, name: str) -> list[str]:
    info = root / "var/lib/dpkg/info"
    for cand in (info / f"{name}.list", *info.glob(f"{name}:*.list")):
        if cand.exists():
            return [ln for ln in cand.read_text(encoding="utf-8").splitlines() if ln]
    return []


def remove_packages(root: pathlib.Path, keep: set[str]) -> list[str]:
    removed = []
    installed = installed_packages(root)
    kept_paths: set[str] = set()
    for name in installed:
        if name in keep:
            kept_paths.update(package_files(root, name))
    for name in sorted(set(installed) - keep):
        removed.append(name)
        for p in package_files(root, name):
            if p in kept_paths or p == "/.":
                continue
            target = root / p.lstrip("/")
            if target.is_symlink() or target.is_file():
                target.unlink()
    # Directories only a removed package owned, deepest first.
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        rel = "/" + os.path.relpath(dirpath, root)
        if rel in ("/.", "/") or rel in kept_paths or rel in MOUNT_POINTS:
            continue
        if not os.listdir(dirpath) and not os.path.islink(dirpath):
            os.rmdir(dirpath)
    return removed


def prune_files(root: pathlib.Path, rules: list[dict]) -> None:
    for rule in rules:
        if not rule.get("reason"):
            raise SystemExit("rootfs/prune.toml: an entry has no reason")
        for path in [*([rule["path"]] if "path" in rule else []), *rule.get("paths", [])]:
            t = root / path.lstrip("/")
            if t.is_symlink() or t.is_file():
                t.unlink()
            elif t.is_dir():
                shutil.rmtree(t)
            else:
                raise SystemExit(f"rootfs/prune.toml: {path} is not in the root")


def safe_unpack(archive: pathlib.Path, wanted: dict[str, str], root: pathlib.Path) -> dict[str, str]:
    """Take only the named members, each a regular file. Returns dest -> sha256."""
    import hashlib

    got = {}
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            if m.name.startswith("/") or ".." in pathlib.PurePosixPath(m.name).parts:
                raise SystemExit(f"{archive.name}: {m.name} is an absolute path or has ..")
            if not (m.isreg() or m.isdir()):
                raise SystemExit(f"{archive.name}: {m.name} is a link, device or other special file")
        for member, dest in wanted.items():
            matches = [m for m in members if m.isreg() and pathlib.PurePosixPath(m.name).parts[1:] == (member,)]
            if len(matches) != 1:
                raise SystemExit(f"{archive.name}: expected exactly one {member}, found {len(matches)}")
            data = tar.extractfile(matches[0]).read()
            target = root / dest.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, INSTALL_MODE)
            got[dest] = hashlib.sha256(data).hexdigest()
    return got


def passwd_group(users: dict) -> tuple[str, str]:
    passwd = "".join(f"{u['name']}:x:{u['uid']}:{u['gid']}:{u['name']}:{u['home']}:{u['shell']}\n" for u in users["user"])
    group = "".join(f"{g['name']}:x:{g['gid']}:\n" for g in users["group"])
    return passwd, group


def os_release(variant: str, commit: str) -> str:
    return (
        'NAME="NineLives"\nID=ninelives\nPRETTY_NAME="NineLives"\n'
        f'VARIANT_ID={variant}\nBUILD_ID={commit[:12]}\n'
        'HOME_URL="https://github.com/GetFelix/ninelives"\n'
    )


def write_file(root: pathlib.Path, path: str, data: str | bytes, mode: int = 0o444) -> None:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.write_bytes(data.encode() if isinstance(data, str) else data)
    os.chmod(target, mode)


def normalise(root: pathlib.Path, sde: int) -> None:
    paths = [root]
    for dirpath, dirnames, filenames in os.walk(root):
        paths += [pathlib.Path(dirpath) / n for n in dirnames + filenames]
    for p in paths:
        st = os.lstat(p)
        os.lchown(p, 0, 0)
        if not stat.S_ISLNK(st.st_mode):
            # No write bit for anyone. setuid and setgid stay: the checks must see them.
            os.chmod(p, stat.S_IMODE(st.st_mode) & ~0o222)
        try:
            for attr in os.listxattr(p, follow_symlinks=False):
                os.removexattr(p, attr, follow_symlinks=False)
        except OSError:
            pass
        os.utime(p, (sde, sde), follow_symlinks=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=pathlib.Path, required=True)
    ap.add_argument("--store", type=pathlib.Path, required=True)
    ap.add_argument("--init", type=pathlib.Path, required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--variant", default="production")
    ap.add_argument("--source-date-epoch", type=int, required=True)
    ap.add_argument("--meta", type=pathlib.Path, required=True)
    args = ap.parse_args()
    root, store = args.root, args.store

    lock = ic.load_toml(image_lock.LOCK)
    keep_cfg = ic.load_toml(ic.IMAGES / "packages.toml")["package"]
    keep = {p["name"] for p in keep_cfg}
    locked = {p["name"]: p for p in lock["root"]["package"]}
    snapshot = lock["snapshot"]

    installed = installed_packages(root)
    for name, version in installed.items():
        if name not in locked or locked[name]["version"] != version:
            raise SystemExit(f"installed {name} {version} is not what debian.lock pins")
    missing = keep - set(installed)
    if missing:
        raise SystemExit(f"packages.toml lists {sorted(missing)}, which were not installed")

    # Origins come from the verified .deb files, read before dpkg's database goes.
    origins: dict[str, dict] = {}
    inventory = []
    for name in sorted(keep):
        p = locked[name]
        deb = deb_path(store, snapshot, p)
        if ic.sha256_file(deb) != p["sha256"]:
            raise SystemExit(f"{deb.name} is not the locked file")
        for m in ic.deb_members(deb):
            origins.setdefault(m.path, {**m.as_dict(), "origin": "deb", "package": name})
        inventory.append({k: p[k] for k in ("name", "version", "sha256", "source")})

    removed = remove_packages(root, keep)
    prune_files(root, ic.load_toml(ic.ROOT / "rootfs" / "prune.toml").get("prune", []))
    write_file(root, "/etc/ld.so.conf", LD_SO_CONF)
    subprocess.run(["ldconfig", "-r", str(root)], check=True)
    for r in RESIDUE:
        t = root / r.lstrip("/")
        if t.is_symlink() or t.is_file():
            t.unlink()
        elif t.is_dir():
            shutil.rmtree(t)
    for doc in (root / "usr/share/doc").rglob("*") if (root / "usr/share/doc").is_dir() else []:
        if doc.is_symlink() and not doc.exists():
            doc.unlink()

    components = ic.load_toml(ic.IMAGES / "components.toml")["component"]
    install = ic.load_toml(ic.ROOT / "rootfs" / "components.toml")
    comp_inv = []
    for c in components:
        art = c["artifacts"]["x86_64"]
        archive = store / "components" / art["url"].rsplit("/", 1)[1]
        if ic.sha256_file(archive) != art["sha256"]:
            raise SystemExit(f"{archive.name} is not the pinned file")
        for dest, digest in safe_unpack(archive, install[c["name"]], root).items():
            origins[dest] = {"type": "file", "mode": INSTALL_MODE, "sha256": digest, "origin": "component", "package": c["name"]}
        comp_inv.append({"name": c["name"], "version": c["version"], "sha256": art["sha256"]})

    init_data = args.init.read_bytes()
    write_file(root, "/usr/lib/ninelives/init", init_data, INSTALL_MODE)
    import hashlib
    origins["/usr/lib/ninelives/init"] = {"type": "file", "mode": INSTALL_MODE, "sha256": hashlib.sha256(init_data).hexdigest(),
                                         "origin": "generated", "package": "ninelives-init"}

    users = ic.load_toml(ic.IMAGES / "users.toml")
    passwd, group = passwd_group(users)
    generated = {"/etc/passwd": passwd, "/etc/group": group, "/etc/nsswitch.conf": NSSWITCH, "/usr/lib/os-release": os_release(args.variant, args.commit)}
    for path, text in generated.items():
        write_file(root, path, text)
        origins[path] = {"type": "file", "mode": 0o444, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                         "origin": "generated", "package": "ninelives-image"}
    for path in ("/etc/ld.so.conf", "/etc/ld.so.cache"):
        data = (root / path.lstrip("/")).read_bytes()
        origins[path] = {"type": "file", "mode": 0o444, "sha256": hashlib.sha256(data).hexdigest(), "origin": "generated",
                         "package": "ninelives-image"}
    osr = root / "etc/os-release"
    if not osr.is_symlink():
        osr.unlink(missing_ok=True)
        osr.symlink_to("../usr/lib/os-release")
    origins["/etc/os-release"] = {"type": "symlink", "mode": 0o777, "link": "../usr/lib/os-release", "origin": "generated", "package": "ninelives-image"}
    for link, target in RUN_LINKS.items():
        if os.readlink(root / link.lstrip("/")) != target:
            raise SystemExit(f"{link} does not point to {target}")
        origins[link] = {"type": "symlink", "mode": 0o777, "link": target, "origin": "generated", "package": "ninelives-image"}
    for link, target in TOP_LEVEL_LINKS.items():
        origins[link] = {"type": "symlink", "mode": 0o777, "link": target, "origin": "generated", "package": "ninelives-image"}
    for mp in MOUNT_POINTS:
        (root / mp.lstrip("/")).mkdir(parents=True, exist_ok=True)
        origins[mp] = {"type": "dir", "mode": 0o555, "origin": "generated", "package": "ninelives-image"}

    normalise(root, args.source_date_epoch)
    for mp in MOUNT_POINTS:
        os.chmod(root / mp.lstrip("/"), 0o555)

    args.meta.mkdir(parents=True, exist_ok=True)
    (args.meta / "origins.json").write_text(json.dumps(origins, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (args.meta / "stage.json").write_text(json.dumps({
        "removed_packages": removed,
        "packages": inventory,
        "components": comp_inv,
        "snapshot": snapshot,
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"staged: kept {len(keep)} packages, removed {len(removed)}, {len(origins)} origins")
    return 0


def deb_path(store: pathlib.Path, snapshot: str, p: dict) -> pathlib.Path:
    return store / "mirror" / "archive" / p["archive"] / snapshot / p["filename"]


if __name__ == "__main__":
    sys.exit(main())
