"""Unit tests for scripts/check_kernel_config.py.

Run from the repository root: python3 -m unittest discover -s scripts/tests
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_kernel_config as ckc  # noqa: E402

FIX = SCRIPTS / "tests" / "fixtures" / "kernel"
SCRIPT = SCRIPTS / "check_kernel_config.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--policy", str(FIX / "policy.toml"),
         "--fragments-dir", str(FIX / "fragments"), *args],
        capture_output=True, text=True,
    )


def config_errors(resolved_text: str, arch: str = "x86_64") -> list[str]:
    policy = ckc.load_policy(FIX / "policy.toml", arch)
    resolved = ckc.parse_config(resolved_text, "resolved")
    fragments = {
        p.name: ckc.parse_config(p.read_text(), p.name)
        for p in ckc.fragment_paths(FIX / "fragments", arch, "qemu")
    }
    upstream = {}
    for rel in ("kernel/configs/hardening.config", f"arch/{ckc.SRCARCH[arch]}/configs/hardening.config"):
        path = FIX / "source" / rel
        if path.is_file():
            upstream[rel] = ckc.parse_config(path.read_text(), rel)
    symbols = ckc.kconfig_symbols(FIX / "source", arch)
    return ckc.check_config(resolved, fragments, upstream, policy, symbols)


GOOD = (FIX / "resolved-good.config").read_text()


class ConfigCheck(unittest.TestCase):
    def test_good_config_passes(self):
        self.assertEqual(config_errors(GOOD), [])

    def test_cli_good_config_passes(self):
        r = run("config", "--arch", "x86_64", "--target", "qemu",
                "--config", str(FIX / "resolved-good.config"), "--source", str(FIX / "source"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_io_uring_enabled_fails(self):
        # The seeded failure kernel.md's test plan asks for.
        errors = config_errors(GOOD + "CONFIG_IO_URING=y\n")
        self.assertEqual(errors, ["policy: CONFIG_IO_URING must not be set, is y"])

    def test_cli_io_uring_enabled_fails(self):
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d) / ".config"
            bad.write_text(GOOD + "CONFIG_IO_URING=y\n")
            r = run("config", "--arch", "x86_64", "--target", "qemu",
                    "--config", str(bad), "--source", str(FIX / "source"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFIG_IO_URING must not be set", r.stderr)

    def test_must_set_missing_fails(self):
        errors = config_errors(GOOD.replace("CONFIG_SECURITY=y\n", ""))
        self.assertIn("policy: CONFIG_SECURITY must be y, is not set", errors)

    def test_string_value_compared_exactly(self):
        errors = config_errors(GOOD.replace('"landlock,lockdown,yama"', '"landlock,lockdown,yama,bpf"'))
        self.assertTrue(any("CONFIG_LSM must be" in e for e in errors), errors)

    def test_fragment_line_that_did_not_land_fails(self):
        errors = config_errors(GOOD.replace("CONFIG_MITIGATION_RETPOLINE=y\n", ""))
        self.assertIn(
            "x86_64.config: CONFIG_MITIGATION_RETPOLINE=y did not land, resolved is not set", errors)

    def test_upstream_line_that_did_not_land_fails(self):
        errors = config_errors(GOOD.replace("CONFIG_X86_KERNEL_IBT=y\n", ""))
        self.assertIn(
            "arch/x86/configs/hardening.config: CONFIG_X86_KERNEL_IBT=y did not land, resolved is not set",
            errors)

    def test_upstream_unset_line_that_got_set_fails(self):
        errors = config_errors(GOOD + "CONFIG_PROC_KCORE=y\n")
        self.assertIn(
            "kernel/configs/hardening.config: CONFIG_PROC_KCORE=not set did not land, resolved is y", errors)

    def test_not_applied_entry_that_landed_is_stale(self):
        errors = config_errors(GOOD + "CONFIG_STRICT_MODULE_RWX=y\n")
        self.assertIn("policy: CONFIG_STRICT_MODULE_RWX is listed as not applied but landed; remove the entry",
                      errors)

    def test_misspelt_option_fails(self):
        # The seeded misspelling kernel.md's test plan asks for: an "is not
        # set" line for an option that does not exist would otherwise pass.
        with tempfile.TemporaryDirectory() as d:
            frag = pathlib.Path(d)
            (frag / "common.config").write_text(
                (FIX / "fragments" / "common.config").read_text()
                + "\n# S6: misspelt.\n# CONFIG_IO_URNIG is not set\n")
            (frag / "x86_64.config").write_text((FIX / "fragments" / "x86_64.config").read_text())
            r = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--policy", str(FIX / "policy.toml"),
                 "--fragments-dir", str(frag), "config", "--arch", "x86_64", "--target", "qemu",
                 "--config", str(FIX / "resolved-good.config"), "--source", str(FIX / "source")],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFIG_IO_URNIG is not an option in this kernel tree", r.stderr)

    def test_unlisted_mitigation_fails(self):
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d)
            (src / "arch" / "x86").mkdir(parents=True)
            (src / "Kconfig").write_text((FIX / "source" / "Kconfig").read_text())
            (src / "arch" / "x86" / "Kconfig").write_text(
                (FIX / "source" / "arch" / "x86" / "Kconfig").read_text()
                + "config MITIGATION_NEW\n\tbool \"new\"\n")
            symbols = ckc.kconfig_symbols(src, "x86_64")
        policy = ckc.load_policy(FIX / "policy.toml", "x86_64")
        errors = ckc.check_config(ckc.parse_config(GOOD, "r"), {}, {}, policy, symbols)
        self.assertIn("policy: CONFIG_MITIGATION_NEW exists in this tree but is not in the x86_64 set "
                      "or not_applicable table", errors)

    def test_not_applicable_counts_as_listed_and_must_stay_unset(self):
        policy = ckc.Policy(tomllib.loads(
            '[x86_64]\nall_listed = ["CONFIG_MITIGATION_"]\n'
            '[x86_64.not_applicable]\nCONFIG_MITIGATION_RETPOLINE = "r"\n'), "x86_64")
        symbols = {"CONFIG_MITIGATION_RETPOLINE"}
        self.assertEqual(ckc.check_config({}, {}, {}, policy, symbols), [])
        errors = ckc.check_config({"CONFIG_MITIGATION_RETPOLINE": "y"}, {}, {}, policy, symbols)
        self.assertEqual(errors, ["policy: CONFIG_MITIGATION_RETPOLINE is listed as not applicable but is y"])

    def test_other_architectures_kconfig_is_ignored(self):
        self.assertNotIn("CONFIG_ARM64_ONLY", ckc.kconfig_symbols(FIX / "source", "x86_64"))
        self.assertIn("CONFIG_ARM64_ONLY", ckc.kconfig_symbols(FIX / "source", "aarch64"))

    def test_option_in_two_fragments_fails(self):
        policy = ckc.load_policy(FIX / "policy.toml", "x86_64")
        frags = {"a.config": {"CONFIG_SECURITY": "y"}, "b.config": {"CONFIG_SECURITY": "y"}}
        errors = ckc.check_config(ckc.parse_config(GOOD, "r"), frags, {}, policy)
        self.assertIn("b.config: CONFIG_SECURITY is also in a.config", errors)

    def test_override_of_upstream_needs_an_entry(self):
        policy = ckc.load_policy(FIX / "policy.toml", "x86_64")
        frags = {"a.config": {"CONFIG_STRICT_KERNEL_RWX": ckc.UNSET}}
        up = {"hardening.config": {"CONFIG_STRICT_KERNEL_RWX": "y"}}
        errors = ckc.check_config(ckc.parse_config(GOOD, "r"), frags, up, policy)
        self.assertIn(
            "a.config: CONFIG_STRICT_KERNEL_RWX overrides hardening.config without a policy.toml "
            "upstream_not_applied entry", errors)


class PolicyShape(unittest.TestCase):
    def policy(self, text: str) -> ckc.Policy:
        return ckc.Policy(tomllib.loads(text), "x86_64")

    def test_set_and_unset_conflict(self):
        p = self.policy('[common]\nunset = ["CONFIG_A"]\n[common.set]\nCONFIG_A = "y"\n')
        self.assertIn("policy: CONFIG_A is both set and unset", p.errors)

    def test_arch_contradicting_common(self):
        p = self.policy('[common.set]\nCONFIG_A = "y"\n[x86_64.set]\nCONFIG_A = "n"\n')
        self.assertIn("policy: CONFIG_A set to 'y' and 'n'", p.errors)

    def test_bad_option_name(self):
        p = self.policy('[common]\nunset = ["IO_URING"]\n')
        self.assertIn("policy: 'IO_URING' is not a CONFIG_ option name", p.errors)

    def test_required_word_that_is_an_image_parameter(self):
        p = self.policy('[common.cmdline]\nrequired = ["console=ttyS0"]\nimage_keys = ["console"]\n')
        self.assertIn("policy: required word 'console=ttyS0' is also an image parameter", p.errors)

    def test_image_prefix_must_end_with_a_dot(self):
        p = self.policy('[common.cmdline]\nimage_prefixes = ["dm"]\n')
        self.assertIn("policy: image prefix 'dm' must end with '.'", p.errors)


class Fragments(unittest.TestCase):
    def lint(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "f.config"
            path.write_text(text)
            return ckc.lint_fragment(path)

    def test_option_without_reason_fails(self):
        errors = self.lint("# Header.\n\nCONFIG_A=y\n")
        self.assertEqual(len(errors), 1)
        self.assertIn(":3: option without a", errors[0])

    def test_comment_without_who_is_not_a_reason(self):
        self.assertEqual(len(self.lint("# just a note\nCONFIG_A=y\n")), 1)

    def test_unset_line_without_reason_fails(self):
        self.assertEqual(len(self.lint("\n# CONFIG_A is not set\n")), 1)

    def test_group_under_one_reason_passes(self):
        self.assertEqual(self.lint("# S2: why.\n# more why.\nCONFIG_A=y\n# CONFIG_B is not set\n"), [])

    def test_blank_line_ends_the_group(self):
        self.assertEqual(len(self.lint("# S2: why.\nCONFIG_A=y\n\nCONFIG_B=y\n")), 1)

    def test_comment_between_options_fails(self):
        errors = self.lint("# S2: why.\nCONFIG_A=y\n# S3: other.\nCONFIG_B=y\n")
        self.assertTrue(any("comment after options" in e for e in errors), errors)

    def test_garbage_line_fails(self):
        self.assertTrue(self.lint("# S2: why.\nCONFIG_A y\n"))

    def test_repository_fragments_and_policy_lint_clean(self):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "lint"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class Cmdline(unittest.TestCase):
    GOOD = ('init=/usr/lib/ninelives/init console=ttyS0,115200 ro '
            'dm-mod.create="root,,,ro,0 8 verity 1 /dev/vda2 /dev/vda3 4096 4096 1 0 sha256 aa bb" '
            'panic=10 pti=on sysctl.kernel.kptr_restrict=2\n')

    def errors(self, text: str, arch: str = "x86_64") -> list[str]:
        return ckc.check_cmdline(text, ckc.load_policy(FIX / "policy.toml", arch))

    def test_good_line_passes(self):
        self.assertEqual(self.errors(self.GOOD), [])

    def test_quoted_value_is_one_word(self):
        words = ckc.split_cmdline(self.GOOD)
        self.assertIn("dm-mod.create=root,,,ro,0 8 verity 1 /dev/vda2 /dev/vda3 4096 4096 1 0 sha256 aa bb",
                      words)

    def test_missing_word_fails(self):
        self.assertEqual(self.errors(self.GOOD.replace(" pti=on", "")),
                         ["cmdline: missing required word 'pti=on'"])

    def test_arch_word_not_required_elsewhere(self):
        self.assertEqual(self.errors(self.GOOD.replace(" pti=on", ""), "aarch64"), [])

    def test_unlisted_words_fail(self):
        # Each of these got past the old denylist; the allowlist rejects them all.
        for word in ("nokaslr", "lsm=selinux", "mitigations=off", "spectre_v2=off", "rw",
                     "mitigations=auto,no_user_kernel", "slub_merge", "init_on_free=n",
                     "init_on_free=no", "init_on_free=false", "init-on-free=0",
                     "clearcpuid=smep", "id_aa64pfr1.bt=0", "dm_verity.require_signatures=0"):
            with self.subTest(word=word):
                self.assertIn(f"cmdline: {word!r} is neither required nor an image parameter policy.toml allows",
                              self.errors(self.GOOD + " " + word))

    def test_unlisted_words_fail_on_aarch64_too(self):
        line = self.GOOD.replace(" pti=on", "") + " id_aa64pfr1.bt=0"
        self.assertEqual(self.errors(line, "aarch64"),
                         ["cmdline: 'id_aa64pfr1.bt=0' is neither required nor an image parameter policy.toml allows"])

    def test_dashes_and_underscores_compare_alike(self):
        # The kernel reads dm_mod.x as dm-mod.x, so a dashed spelling can neither
        # slip past a required key nor be refused where the underscore is allowed.
        self.assertEqual(self.errors(self.GOOD + " dm_mod.waitfor=/dev/vda2"), [])
        self.assertNotEqual(self.errors(self.GOOD + " sysctl.kernel.kptr-restrict=0"), [])

    def test_sysctl_keys_are_compared_exactly(self):
        # The kernel maps sysctl.* to a /proc/sys path as written, so the dashed
        # spelling sets nothing and must not satisfy the required one.
        errors = self.errors(self.GOOD.replace("sysctl.kernel.kptr_restrict=2",
                                               "sysctl.kernel.kptr-restrict=2"))
        self.assertNotEqual(errors, [])
        self.assertTrue(any("sysctl.kernel.kptr_restrict=2" in e for e in errors), errors)

    def test_repository_policy_takes_an_image_line(self):
        for arch in ckc.SRCARCH:
            policy = ckc.load_policy(ckc.KERNEL / "policy.toml", arch)
            line = ('root=/dev/dm-0 rootfstype=erofs init=/usr/lib/ninelives/init console=ttyS0 '
                    'dm-mod.create="root,,,ro,0 8 verity 1 /dev/vda2 /dev/vda3 4096 4096 1 0 sha256 aa bb" '
                    + " ".join(policy.required))
            with self.subTest(arch=arch):
                self.assertEqual(ckc.check_cmdline(line, policy), [])
                self.assertEqual(len(ckc.check_cmdline(line + " init_on_free=n", policy)), 1)

    def test_flag_word_with_a_value_conflicts(self):
        self.assertIn("cmdline: 'ro=0' conflicts with required 'ro'", self.errors(self.GOOD + " ro=0"))

    def test_later_override_of_required_value_fails(self):
        self.assertIn("cmdline: 'panic=0' conflicts with required 'panic=10'",
                      self.errors(self.GOOD + " panic=0"))

    def test_sysctl_with_other_value_fails(self):
        self.assertIn(
            "cmdline: 'sysctl.kernel.kptr_restrict=1' conflicts with required 'sysctl.kernel.kptr_restrict=2'",
            self.errors(self.GOOD + " sysctl.kernel.kptr_restrict=1"))

    def test_unlisted_sysctl_fails(self):
        self.assertIn("cmdline: 'sysctl.kernel.panic_on_warn=1' is neither required nor an image parameter "
                      "policy.toml allows",
                      self.errors(self.GOOD + " sysctl.kernel.panic_on_warn=1"))

    def test_words_after_double_dash_do_not_count(self):
        line = self.GOOD.replace(" pti=on", "") + " -- pti=on"
        self.assertIn("cmdline: missing required word 'pti=on'", self.errors(line))

    def test_cli_seeded_missing_and_unlisted(self):
        # The seeded line kernel.md's test plan asks for: one word missing, one not allowed.
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "cmdline"
            path.write_text(self.GOOD.replace(" panic=10", "") + " nokaslr\n")
            r = run("cmdline", "--arch", "x86_64", "--cmdline", str(path))
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing required word 'panic=10'", r.stderr)
        self.assertIn("'nokaslr' is neither required nor an image parameter", r.stderr)

    def test_emitted_line_passes(self):
        r = run("emit", "--arch", "x86_64", "cmdline")
        self.assertEqual(self.errors(r.stdout), [])


class Hardening(unittest.TestCase):
    def setUp(self):
        self.report = json.loads((FIX / "hardening-report.json").read_text())
        self.exceptions = tomllib.loads((FIX / "hardening-exceptions.toml").read_text())["exception"]

    def test_unexcused_failure_fails(self):
        errors = ckc.check_hardening(self.report, self.exceptions, "x86_64")
        self.assertEqual(errors, ["kernel-hardening-checker: cmdline panic_on_warn: FAIL: is not found"])

    def test_exception_is_per_architecture(self):
        errors = ckc.check_hardening(self.report, self.exceptions, "aarch64")
        self.assertIn("kernel-hardening-checker: kconfig CONFIG_RANDSTRUCT_FULL: FAIL: is not found", errors)

    def test_exception_that_now_passes_is_stale(self):
        exc = [{"option": "CONFIG_BUG", "arch": ["x86_64"], "reason": "r"}]
        self.assertIn("hardening-exceptions: CONFIG_BUG now passes; remove the exception",
                      ckc.check_hardening(self.report[:1], exc, "x86_64"))

    def test_exception_for_unknown_option_is_stale(self):
        exc = [{"option": "CONFIG_GONE", "arch": ["x86_64"], "reason": "r"}]
        self.assertIn("hardening-exceptions: CONFIG_GONE is not in the checker's report; remove or rename it",
                      ckc.check_hardening(self.report[:1], exc, "x86_64"))

    def test_exception_needs_a_reason(self):
        exc = [{"option": "CONFIG_RANDSTRUCT_FULL", "arch": ["x86_64"], "reason": " "}]
        self.assertIn("hardening-exceptions: CONFIG_RANDSTRUCT_FULL has no reason",
                      ckc.check_hardening(self.report[:2], exc, "x86_64"))

    def test_one_failing_entry_of_a_repeated_option_fails(self):
        report = self.report[:1] + [dict(self.report[0], check_result="FAIL: no", check_result_bool=False)]
        self.assertEqual(ckc.check_hardening(report, [], "x86_64"),
                         ["kernel-hardening-checker: kconfig CONFIG_BUG: FAIL: no"])


if __name__ == "__main__":
    unittest.main()
