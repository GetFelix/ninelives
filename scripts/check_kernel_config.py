#!/usr/bin/env python3
"""Check the kernel config fragments, a resolved .config and a command line
against kernel/policy.toml (docs/components/kernel.md, "What CI checks").

This resolves nothing itself. CI runs Kconfig on the pinned tree and hands
the result here; every subcommand only reads files.

  lint        fragment comments and policy.toml shape; needs no kernel tree
  fragments   print the fragment paths for an architecture, in merge order
  config      a resolved .config against the fragments and policy.toml
  cmdline     a kernel command line against policy.toml
  hardening   kernel-hardening-checker's JSON report against the exceptions
  emit        the policy's command line words or sysctls, for the checker
"""
import argparse
import json
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL = ROOT / "kernel"
# Our architecture names and the kernel's.
SRCARCH = {"x86_64": "x86", "aarch64": "arm64"}
TARGETS = ("qemu",)

SET_LINE = re.compile(r"(CONFIG_\w+)=(.*)")
UNSET_LINE = re.compile(r"# (CONFIG_\w+) is not set")
# "# <who>: <why>", where <who> names a requirement, component or target.
REASON = re.compile(r"# [\w ,./+-]{1,60}: \S")
UNSET = None


def parse_config(text: str, where: str, errors: list[str] | None = None) -> dict[str, str | None]:
    """Map each option to its value, or UNSET for '# ... is not set'."""
    values: dict[str, str | None] = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if m := SET_LINE.fullmatch(line):
            values[m.group(1)] = m.group(2)
        elif m := UNSET_LINE.fullmatch(line):
            values[m.group(1)] = UNSET
        elif line and not line.startswith("#") and errors is not None:
            errors.append(f"{where}:{n}: not an option line: {line!r}")
    return values


def show(value: str | None) -> str:
    return "not set" if value is UNSET else value


def fragment_paths(directory: pathlib.Path, arch: str, target: str) -> list[pathlib.Path]:
    names = ["common", arch, target, f"{target}-{arch}"]
    paths = [directory / f"{n}.config" for n in names]
    return [p for p in paths if p.is_file()]


def lint_fragment(path: pathlib.Path) -> list[str]:
    """Every option line belongs to a group headed by a reason comment."""
    errors = []
    where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    reason_ok = False
    seen_option = False
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line:
            reason_ok = seen_option = False
            continue
        if SET_LINE.fullmatch(line) or UNSET_LINE.fullmatch(line):
            if not reason_ok:
                errors.append(f"{where}:{n}: option without a '# <who>: <why>' comment above it")
            seen_option = True
        elif line.startswith("#"):
            if seen_option:
                errors.append(f"{where}:{n}: comment after options; start a new group with a blank line")
            elif not reason_ok and REASON.match(line):
                reason_ok = True
        else:
            errors.append(f"{where}:{n}: not an option or a comment: {line!r}")
    return errors


class Policy:
    """policy.toml's common tables merged with one architecture's."""

    def __init__(self, data: dict, arch: str):
        common, own = data.get("common", {}), data.get(arch, {})
        self.arch = arch
        self.errors: list[str] = []
        self.set: dict[str, str] = {}
        for part in (common, own):
            for sym, value in part.get("set", {}).items():
                if sym in self.set and self.set[sym] != value:
                    self.errors.append(f"policy: {sym} set to {self.set[sym]!r} and {value!r}")
                self.set[sym] = value
        self.unset = set(common.get("unset", [])) | set(own.get("unset", []))
        for sym in sorted(self.set.keys() & self.unset):
            self.errors.append(f"policy: {sym} is both set and unset")
        self.not_applied = {**common.get("upstream_not_applied", {}), **own.get("upstream_not_applied", {})}
        self.all_listed = common.get("all_listed", []) + own.get("all_listed", [])
        self.not_applicable = {**common.get("not_applicable", {}), **own.get("not_applicable", {})}
        self.sysctl = {**common.get("sysctl", {}), **own.get("sysctl", {})}
        cc, oc = common.get("cmdline", {}), own.get("cmdline", {})
        self.required = cc.get("required", []) + oc.get("required", [])
        self.required += [f"sysctl.{k}={v}" for k, v in self.sysctl.items()]
        self.image_keys = {param_key(k) for k in cc.get("image_keys", []) + oc.get("image_keys", [])}
        self.image_prefixes = [param_key(p) for p in cc.get("image_prefixes", []) + oc.get("image_prefixes", [])]
        for sym in sorted(self.set.keys() & self.not_applicable.keys()):
            self.errors.append(f"policy: {sym} is both set and not applicable")
        for sym in [*self.set, *self.unset, *self.not_applied, *self.not_applicable]:
            if not re.fullmatch(r"CONFIG_[A-Z0-9_]+", sym):
                self.errors.append(f"policy: {sym!r} is not a CONFIG_ option name")
        for p in self.image_prefixes:
            if not p.endswith("."):
                self.errors.append(f"policy: image prefix {p!r} must end with '.'")
        for word in self.required:
            if self.image_owned(param_key(word)):
                self.errors.append(f"policy: required word {word!r} is also an image parameter")

    def image_owned(self, key: str) -> bool:
        return key in self.image_keys or any(key.startswith(p) for p in self.image_prefixes)


def param_key(word: str) -> str:
    """A parameter's name as the kernel compares it: dashes and underscores alike.

    sysctl.* is the exception: the kernel maps it to a /proc/sys path as written.
    """
    return _fold(word.split("=", 1)[0])


def _fold(key: str) -> str:
    return key if key.startswith("sysctl.") else key.replace("-", "_")


def normalize_word(word: str) -> str:
    key, sep, value = word.partition("=")
    return _fold(key) + sep + value


def load_policy(path: pathlib.Path, arch: str) -> Policy:
    return Policy(tomllib.loads(path.read_text(encoding="utf-8")), arch)


def kconfig_symbols(source: pathlib.Path, arch: str) -> set[str]:
    """Every option defined for this architecture in a kernel tree."""
    symbols = set()
    for p in source.rglob("Kconfig*"):
        rel = p.relative_to(source).parts
        if rel[0] == "arch" and len(rel) > 2 and rel[1] != SRCARCH[arch]:
            continue
        for m in re.finditer(r"^\s*(?:menu)?config\s+(\w+)\s*$", p.read_text(errors="replace"), re.M):
            symbols.add("CONFIG_" + m.group(1))
    return symbols


def check_config(
    resolved: dict[str, str | None],
    fragments: dict[str, dict[str, str | None]],
    upstream: dict[str, dict[str, str | None]],
    policy: Policy,
    symbols: set[str] | None = None,
) -> list[str]:
    errors = list(policy.errors)

    def landed(sym: str, want: str | None) -> bool:
        return resolved.get(sym, UNSET) == want

    for sym, want in sorted(policy.set.items()):
        if not landed(sym, want):
            errors.append(f"policy: {sym} must be {want}, is {show(resolved.get(sym, UNSET))}")
    for sym in sorted(policy.unset):
        if not landed(sym, UNSET):
            errors.append(f"policy: {sym} must not be set, is {resolved[sym]}")
    for sym in sorted(policy.not_applicable):
        if not landed(sym, UNSET):
            errors.append(f"policy: {sym} is listed as not applicable but is {resolved[sym]}")

    # Check 2: every line we wrote is in the resolved config as written.
    ours: dict[str, tuple[str, str | None]] = {}
    for name, values in fragments.items():
        for sym, want in values.items():
            if sym in ours:
                errors.append(f"{name}: {sym} is also in {ours[sym][0]}")
            ours[sym] = (name, want)
            if not landed(sym, want):
                errors.append(f"{name}: {sym}={show(want)} did not land, resolved is {show(resolved.get(sym, UNSET))}")

    upstream_lines: dict[str, str | None] = {}
    for name, values in upstream.items():
        for sym, want in values.items():
            upstream_lines[sym] = want
            if sym in ours and ours[sym][1] != want and sym not in policy.not_applied:
                errors.append(f"{ours[sym][0]}: {sym} overrides {name} without a policy.toml upstream_not_applied entry")
            if sym in policy.not_applied:
                if landed(sym, want):
                    errors.append(f"policy: {sym} is listed as not applied but landed; remove the entry")
            elif not landed(sym, want):
                errors.append(f"{name}: {sym}={show(want)} did not land, resolved is {show(resolved.get(sym, UNSET))}")
    for sym in sorted(policy.not_applied.keys() - upstream_lines.keys()):
        errors.append(f"policy: upstream_not_applied lists {sym}, which no upstream fragment sets")

    if symbols is not None:
        # upstream_not_applied may name an option this architecture lacks.
        named = set(policy.set) | policy.unset | set(policy.not_applicable) | set(ours)
        for sym in sorted(named - symbols):
            errors.append(f"{sym} is not an option in this kernel tree (misspelt or renamed upstream?)")
        for prefix in policy.all_listed:
            for sym in sorted(s for s in symbols if s.startswith(prefix)):
                if sym not in policy.set and sym not in policy.not_applicable:
                    errors.append(f"policy: {sym} exists in this tree but is not in the {policy.arch} set "
                                  "or not_applicable table")
    return errors


def split_cmdline(text: str) -> list[str]:
    """Split like the kernel's next_arg: whitespace outside double quotes."""
    words, cur, quoted, started = [], [], False, False
    for ch in text:
        if ch == '"':
            quoted, started = not quoted, True
        elif ch.isspace() and not quoted:
            if started:
                words.append("".join(cur))
            cur, started = [], False
        else:
            cur.append(ch)
            started = True
    if started:
        words.append("".join(cur))
    return words


def check_cmdline(text: str, policy: Policy) -> list[str]:
    errors = list(policy.errors)
    words = split_cmdline(text)
    # The kernel hands everything after "--" to init without reading it.
    if "--" in words:
        words = words[: words.index("--")]
    # An allowlist: every word is either one the kernel policy requires or a
    # parameter the image design owns. A denylist misses spellings the kernel
    # also accepts (init-on-free=0, init_on_free=n, mitigations=auto,...).
    normalized = {normalize_word(w) for w in words}
    required = {param_key(w): w for w in policy.required}
    for word in policy.required:
        if normalize_word(word) not in normalized:
            errors.append(f"cmdline: missing required word {word!r}")
    for word in words:
        key = param_key(word)
        if key in required:
            if normalize_word(word) != normalize_word(required[key]):
                errors.append(f"cmdline: {word!r} conflicts with required {required[key]!r}")
        elif not policy.image_owned(key):
            errors.append(f"cmdline: {word!r} is neither required nor an image parameter policy.toml allows")
    return errors


def check_hardening(report: list[dict], exceptions: list[dict], arch: str) -> list[str]:
    errors = []
    results: dict[tuple[str, str], bool] = {}
    for item in report:
        key = (item["type"], item["option_name"])
        results[key] = results.get(key, True) and item["check_result_bool"]
    excused = {}
    for e in exceptions:
        if arch not in e.get("arch", []):
            continue
        if not e.get("reason", "").strip():
            errors.append(f"hardening-exceptions: {e.get('option')} has no reason")
        excused[(e.get("type", "kconfig"), e.get("option"))] = e
    for key, ok in sorted(results.items()):
        if not ok and key not in excused:
            fails = [i["check_result"] for i in report if (i["type"], i["option_name"]) == key and not i["check_result_bool"]]
            errors.append(f"kernel-hardening-checker: {key[0]} {key[1]}: {'; '.join(fails)}")
    for key in sorted(excused):
        if key not in results:
            errors.append(f"hardening-exceptions: {key[1]} is not in the checker's report; remove or rename it")
        elif results[key]:
            errors.append(f"hardening-exceptions: {key[1]} now passes; remove the exception")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", type=pathlib.Path, default=KERNEL / "policy.toml")
    parser.add_argument("--fragments-dir", type=pathlib.Path, default=KERNEL / "fragments")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("lint")
    p = sub.add_parser("fragments")
    p.add_argument("--arch", choices=SRCARCH, required=True)
    p.add_argument("--target", choices=TARGETS, required=True)
    p = sub.add_parser("config")
    p.add_argument("--arch", choices=SRCARCH, required=True)
    p.add_argument("--target", choices=TARGETS, required=True)
    p.add_argument("--config", type=pathlib.Path, required=True, help="the resolved .config")
    p.add_argument("--source", type=pathlib.Path, required=True, help="the kernel tree it was resolved in")
    p = sub.add_parser("cmdline")
    p.add_argument("--arch", choices=SRCARCH, required=True)
    p.add_argument("--cmdline", type=pathlib.Path, required=True)
    p = sub.add_parser("hardening")
    p.add_argument("--arch", choices=SRCARCH, required=True)
    p.add_argument("--report", type=pathlib.Path, required=True, help="kernel-hardening-checker -m json output")
    p.add_argument("--exceptions", type=pathlib.Path, default=KERNEL / "hardening-exceptions.toml")
    p = sub.add_parser("emit")
    p.add_argument("--arch", choices=SRCARCH, required=True)
    p.add_argument("what", choices=["cmdline", "sysctl"])
    args = parser.parse_args()

    errors: list[str] = []
    if args.command == "lint":
        for path in sorted(args.fragments_dir.glob("*.config")):
            errors += lint_fragment(path)
            parse_config(path.read_text(encoding="utf-8"), path.name, errors)
        for arch in SRCARCH:
            errors += load_policy(args.policy, arch).errors
    elif args.command == "fragments":
        print("\n".join(str(p) for p in fragment_paths(args.fragments_dir, args.arch, args.target)))
    elif args.command == "config":
        policy = load_policy(args.policy, args.arch)
        resolved = parse_config(args.config.read_text(encoding="utf-8"), str(args.config))
        fragments = {}
        for path in fragment_paths(args.fragments_dir, args.arch, args.target):
            errors += lint_fragment(path)
            fragments[path.name] = parse_config(path.read_text(encoding="utf-8"), path.name, errors)
        upstream = {}
        for rel in ("kernel/configs/hardening.config", f"arch/{SRCARCH[args.arch]}/configs/hardening.config"):
            path = args.source / rel
            if path.is_file():
                upstream[rel] = parse_config(path.read_text(encoding="utf-8"), rel)
        errors += check_config(resolved, fragments, upstream, policy, kconfig_symbols(args.source, args.arch))
    elif args.command == "cmdline":
        errors = check_cmdline(args.cmdline.read_text(encoding="utf-8"), load_policy(args.policy, args.arch))
    elif args.command == "hardening":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        exceptions = tomllib.loads(args.exceptions.read_text(encoding="utf-8")).get("exception", [])
        errors = check_hardening(report, exceptions, args.arch)
    elif args.command == "emit":
        policy = load_policy(args.policy, args.arch)
        if args.what == "cmdline":
            print(" ".join(policy.required))
        else:
            print("\n".join(f"{k} = {v}" for k, v in policy.sysctl.items()))
        errors = policy.errors

    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
