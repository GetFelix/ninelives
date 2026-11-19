#!/usr/bin/env python3
"""Check decision 0005's rule: `unsafe` lives only in a module named `sys`.

For every crate under the given directories:

- `unsafe` appears in code only in `src/sys.rs` or under `src/sys/`, and
  there every use has a `// SAFETY:` comment directly above it or on its line;
- every crate root (`src/main.rs`, `src/lib.rs`, `tests/*.rs`) has
  `#![deny(unsafe_code)]`;
- outside `sys`, the `unsafe_code` lint is named only by that `deny` and by
  `#[allow(unsafe_code)]` on the declaration of `mod sys`;
- the workspace sets `unsafe_code = "deny"`, every package inherits the
  workspace lints, and no manifest or cargo config relaxes the lint.

Comments and string literals are stripped before matching, so prose about
`unsafe` does not count. `--self-test` runs the checks on built-in cases.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

UNSAFE = re.compile(r"\bunsafe\b")
LINT = re.compile(r"\bunsafe_code\b")
CRATE_DENY = re.compile(r"#!\[\s*deny\(\s*unsafe_code\s*\)\s*\]")
# The allow, then any other attributes (a cfg), then `mod sys;`.
SYS_ALLOW = re.compile(
    r"#\[\s*allow\(\s*unsafe_code\s*\)\s*\]\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?mod\s+sys\s*;"
)


def strip(src: str) -> str:
    """Blank out comments, strings and char literals, keeping line breaks."""
    out = []
    i, n = 0, len(src)

    def blank(text: str) -> str:
        return "".join(c if c == "\n" else " " for c in text)

    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(blank(src[i:j]))
            i = j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            out.append(blank(src[i:j]))
            i = j
        elif (m := re.match(r'b?r(#*)"', src[i:])) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            end = '"' + m.group(1)
            j = src.find(end, i + m.end())
            j = n if j < 0 else j + len(end)
            out.append(blank(src[i:j]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append(blank(src[i : j + 1]))
            i = j + 1
        elif c == "'":
            # A char literal ('x', '\n', '\''), or else a lifetime, kept as is.
            m = re.match(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]+\}|.)|[^\\'])'", src[i:])
            if m:
                out.append(blank(m.group(0)))
                i += m.end()
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def in_sys(path: Path, crate: Path) -> bool:
    rel = path.relative_to(crate).parts
    return rel[:1] == ("src",) and (rel[1:] == ("sys.rs",) or rel[1:2] == ("sys",))


def crate_roots(crate: Path) -> list[Path]:
    roots = [crate / "src" / name for name in ("main.rs", "lib.rs")]
    roots += sorted((crate / "tests").glob("*.rs"))
    return [p for p in roots if p.is_file()]


def has_safety_comment(raw_lines: list[str], index: int) -> bool:
    if "// SAFETY:" in raw_lines[index]:
        return True
    j = index - 1
    while j >= 0:
        line = raw_lines[j].strip()
        if line.startswith("//"):
            if line.startswith("// SAFETY:"):
                return True
            j -= 1
        elif line.startswith("#["):
            j -= 1
        else:
            return False
    return False


def check_rust(path: Path, crate: Path, errors: list[str]) -> None:
    raw = path.read_text(encoding="utf-8")
    code = strip(raw)
    raw_lines = raw.split("\n")
    sys_file = in_sys(path, crate)
    for index, line in enumerate(code.split("\n")):
        if UNSAFE.search(line):
            where = f"{path}:{index + 1}"
            if not sys_file:
                errors.append(f"{where}: `unsafe` outside a `sys` module")
            elif not has_safety_comment(raw_lines, index):
                errors.append(f"{where}: `unsafe` without a `// SAFETY:` comment")
    if sys_file:
        return
    allowed = [m.span() for m in CRATE_DENY.finditer(code)]
    allowed += [m.span() for m in SYS_ALLOW.finditer(code)]
    for m in LINT.finditer(code):
        if not any(a <= m.start() < b for a, b in allowed):
            line = code.count("\n", 0, m.start()) + 1
            errors.append(f"{path}:{line}: `unsafe_code` named outside `#![deny]` and `mod sys`")


def check_crate(crate: Path, errors: list[str]) -> None:
    for root in crate_roots(crate):
        if not CRATE_DENY.search(strip(root.read_text(encoding="utf-8"))):
            errors.append(f"{root}: crate root lacks #![deny(unsafe_code)]")
    for path in sorted(crate.rglob("*.rs")):
        if "target" in path.relative_to(crate).parts:
            continue
        check_rust(path, crate, errors)


def manifest_lint_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if LINT.search(line.split("#", 1)[0])]


def check_tree(top: Path) -> list[str]:
    errors: list[str] = []
    manifests = [p for p in sorted(top.rglob("Cargo.toml")) if "target" not in p.relative_to(top).parts]
    if not manifests:
        errors.append(f"{top}: no Cargo.toml found")
    workspace_deny = False
    for manifest in manifests:
        text = manifest.read_text(encoding="utf-8")
        lines = manifest_lint_lines(text)
        is_workspace = "[workspace.lints.rust]" in text
        if is_workspace and lines == ['unsafe_code = "deny"']:
            workspace_deny = True
        elif lines:
            errors.append(f"{manifest}: names unsafe_code outside [workspace.lints.rust] deny")
        if "[package]" in text:
            if not re.search(r"^\[lints\]\s*\n\s*workspace\s*=\s*true", text, re.M):
                errors.append(f"{manifest}: package does not inherit the workspace lints")
            check_crate(manifest.parent, errors)
    if manifests and not workspace_deny:
        errors.append(f"{top}: no workspace sets unsafe_code = \"deny\"")
    for config in [*top.rglob(".cargo/config"), *top.rglob(".cargo/config.toml")]:
        if LINT.search(config.read_text(encoding="utf-8")):
            errors.append(f"{config}: cargo config names unsafe_code")
    return errors


def self_test() -> None:
    manifest = (
        '[package]\nname = "x"\n\n[lints]\nworkspace = true\n\n'
        '[workspace]\n\n[workspace.lints.rust]\nunsafe_code = "deny"\n'
    )
    main_ok = (
        "#![deny(unsafe_code)]\n"
        "// unsafe in a comment is fine, and so is \"unsafe\" in a string.\n"
        'const S: &str = "unsafe { }";\n'
        "#[cfg(target_os = \"linux\")]\n#[allow(unsafe_code)]\nmod sys;\n"
        "fn f<'a>(x: &'a u8) -> char { let _ = x; '\"' }\n"
    )
    sys_ok = "// SAFETY: fine.\n#[unsafe(no_mangle)]\nfn g() {}\nfn h() {\n    // SAFETY: fine.\n    unsafe { }\n}\n"
    cases = {
        "clean": (main_ok, sys_ok, 0),
        "unsafe outside sys": (main_ok + "fn k() { unsafe { } }\n", sys_ok, 1),
        "missing SAFETY": (main_ok, "fn h() {\n    unsafe { }\n}\n", 1),
        "allow elsewhere": (main_ok + "#[allow(unsafe_code)]\nmod other;\n", sys_ok, 1),
        "cfg_attr allow": (main_ok + "#[cfg_attr(test, allow(unsafe_code))]\nfn z() {}\n", sys_ok, 1),
        "no deny": (main_ok.replace("#![deny(unsafe_code)]\n", ""), sys_ok, 1),
        "raw string": (main_ok + 'const R: &str = r#"unsafe"#;\n', sys_ok, 0),
    }
    failed = []
    for name, (main_rs, sys_rs, want) in cases.items():
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src").mkdir()
            (root / "Cargo.toml").write_text(manifest)
            (root / "src" / "main.rs").write_text(main_rs)
            (root / "src" / "sys.rs").write_text(sys_rs)
            got = len(check_tree(root))
            if got != want:
                failed.append(f"{name}: {got} errors, want {want}: {check_tree(root)}")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "src").mkdir()
        (root / "Cargo.toml").write_text(manifest + '\n[lints.rust]\nunsafe_code = "allow"\n')
        (root / "src" / "main.rs").write_text(main_ok)
        if not check_tree(root):
            failed.append("manifest override: not caught")
    if failed:
        sys.exit("self-test failed:\n" + "\n".join(failed))
    print(f"self-test: {len(cases) + 1} cases pass")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("dirs", nargs="*", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    errors = [e for d in args.dirs for e in check_tree(d)]
    if errors:
        sys.exit("\n".join(errors))


if __name__ == "__main__":
    main()
