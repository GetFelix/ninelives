"""One build, two architectures: the pins, locks and target files agree, and x86_64 is unchanged."""
import os
import pathlib
import sys
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import image_common as ic  # noqa: E402
import image_lock  # noqa: E402

ARCHES = sorted(ic.DEB_ARCH)


def target(arch: str) -> dict:
    return ic.load_toml(ic.target_path(arch))


class Selection(unittest.TestCase):
    def test_x86_64_is_the_default(self):
        with mock.patch.dict(os.environ, clear=False) as env:
            env.pop("NL_ARCH", None)
            self.assertEqual(ic.target_arch(), "x86_64")
            self.assertEqual(ic.lock_path(), ic.IMAGES / "debian.lock")

    def test_aarch64_has_its_own_lock_and_target(self):
        with mock.patch.dict(os.environ, {"NL_ARCH": "aarch64"}):
            self.assertEqual(ic.lock_path(), ic.IMAGES / "debian-aarch64.lock")
            self.assertEqual(ic.target_path(), ic.IMAGES / "qemu-aarch64" / "target.toml")

    def test_an_unknown_architecture_is_refused(self):
        with mock.patch.dict(os.environ, {"NL_ARCH": "riscv64"}):
            with self.assertRaises(SystemExit):
                ic.target_arch()

    def test_arch_section_overrides_only_for_that_architecture(self):
        sec = {"release": "trixie", "packages": ["a"], "aarch64": {"packages": ["b"]}}
        self.assertEqual(ic.arch_section(sec, "x86_64"), {"release": "trixie", "packages": ["a"]})
        self.assertEqual(ic.arch_section(sec, "aarch64"), {"release": "trixie", "packages": ["b"]})

    def test_for_arch_keeps_entries_without_a_restriction(self):
        entries = [{"path": "/a"}, {"path": "/b", "arches": ["x86_64"]}]
        self.assertEqual([e["path"] for e in ic.for_arch(entries, "x86_64")], ["/a", "/b"])
        self.assertEqual([e["path"] for e in ic.for_arch(entries, "aarch64")], ["/a"])


class Locks(unittest.TestCase):
    def test_each_lock_is_for_its_architecture(self):
        for arch in ARCHES:
            lock = ic.load_toml(ic.lock_path(arch))
            self.assertEqual(lock["architecture"], ic.DEB_ARCH[arch], arch)
            for which in image_lock.SETS:
                for p in lock[which]["package"]:
                    self.assertIn(p["architecture"], (ic.DEB_ARCH[arch], "all"), f"{arch} {which} {p['name']}")

    def test_the_boot_set_is_the_architectures_own(self):
        tools = ic.load_toml(ic.IMAGES / "tools.toml")
        for arch, qemu in (("x86_64", "qemu-system-x86"), ("aarch64", "qemu-system-arm")):
            names = {p["name"] for p in ic.load_toml(ic.lock_path(arch))["boot"]["package"]}
            self.assertIn(qemu, names)
            self.assertEqual(set(image_lock.set_packages(tools, "boot", arch)) - names, set())
        self.assertIn("qemu-efi-aarch64", image_lock.set_packages(tools, "boot", "aarch64"))
        self.assertIn("ovmf", image_lock.set_packages(tools, "boot", "x86_64"))

    def test_the_pins_check_passes_for_every_lock(self):
        self.assertEqual(image_lock.check(), [])

    def test_a_lock_of_the_wrong_architecture_is_caught(self):
        tools = ic.load_toml(ic.IMAGES / "tools.toml")
        with mock.patch.object(ic, "lock_path", return_value=ic.lock_path("aarch64")):
            errors = image_lock.check_lock("x86_64", tools, [])
        self.assertTrue(any("architecture is not amd64" in e for e in errors), errors)

    def test_cosign_and_boot_pins_exist_for_both(self):
        tools = ic.load_toml(ic.IMAGES / "tools.toml")
        for arch in ARCHES:
            cs = ic.arch_section(tools["cosign"], arch)
            self.assertRegex(cs["sha256"], r"[0-9a-f]{64}")
            self.assertIn(tools["cosign"]["version"], cs["url"])
            self.assertIn("amd64" if arch == "x86_64" else "arm64", cs["url"])
            boot = ic.arch_section(tools["boot"], arch)
            self.assertTrue({"packages", "qemu", "firmware_code", "firmware_vars"} <= set(boot), arch)
        self.assertNotEqual(ic.arch_section(tools["cosign"], "x86_64")["sha256"], ic.arch_section(tools["cosign"], "aarch64")["sha256"])


class Targets(unittest.TestCase):
    def test_both_targets_say_the_same_things(self):
        self.assertEqual(set(target("x86_64")), set(target("aarch64")))
        self.assertEqual(target("x86_64")["sizes"], target("aarch64")["sizes"])

    def test_each_target_names_its_own_architecture(self):
        for arch in ARCHES:
            self.assertEqual(target(arch)["architecture"], arch)

    def test_aarch64_boots_the_way_the_issue_says(self):
        t = target("aarch64")
        self.assertTrue(t["console"].startswith("ttyAMA0"))
        self.assertEqual(t["efi_boot"], "BOOTAA64.EFI")
        self.assertEqual(t["root_type"], "root-arm64")
        self.assertEqual(t["kernel_file"], "Image")
        self.assertIn("aa64", t["uki_stub"])

    def test_x86_64_is_what_it_was(self):
        t = target("x86_64")
        self.assertEqual((t["console"], t["efi_boot"], t["root_type"], t["kernel_file"]),
                         ("ttyS0,115200", "BOOTX64.EFI", "root-x86-64", "bzImage"))
        self.assertTrue(t["uki_stub"].endswith("linuxx64.efi.stub"))

    def test_seeds_differ_so_no_two_architectures_share_a_uuid_space(self):
        self.assertNotEqual(target("x86_64")["disk_seed"], target("aarch64")["disk_seed"])
        self.assertNotEqual(target("x86_64")["erofs_uuid_seed"], target("aarch64")["erofs_uuid_seed"])

    def test_the_mkosi_confs_name_debians_architecture(self):
        for arch, want in (("x86_64", "x86-64"), ("aarch64", "arm64")):
            text = (ic.IMAGES / f"qemu-{arch}" / "mkosi.conf").read_text()
            self.assertIn(f"Architecture={want}", text)

    def test_every_architecture_has_a_budget(self):
        budget = ic.load_toml(ic.IMAGES / "budget.toml")
        for arch in ARCHES:
            self.assertEqual(set(budget[arch]), {"root_erofs", "uki", "esp_files", "raw"}, arch)


class PerArchitectureChecks(unittest.TestCase):
    def test_the_esp_holds_the_architectures_fallback_loader(self):
        import check_image
        good = ["EFI", "EFI/BOOT", "EFI/BOOT/BOOTAA64.EFI"]
        self.assertEqual(check_image.check_esp(good, b"u", b"u", "BOOTAA64.EFI"), [])
        self.assertTrue(check_image.check_esp(good, b"u", b"u"), "x86_64's name must not accept aarch64's file")

    def test_the_kernel_artifact_names_the_architectures_image(self):
        import image_compare
        for arch, name in (("x86_64", "bzImage"), ("aarch64", "Image")):
            with mock.patch.dict(os.environ, {"NL_ARCH": arch}):
                self.assertEqual(image_compare.kernel_files(), (name, "System.map", ".config"))


class Allowlists(unittest.TestCase):
    def execs(self, arch):
        return ic.for_arch(ic.load_for_target(ic.IMAGES / "executables.toml", target(arch))["executable"], arch)

    def test_x86_64_paths_are_the_ones_it_always_had(self):
        paths = {e["path"] for e in self.execs("x86_64")}
        self.assertIn("/usr/lib/x86_64-linux-gnu/libc.so.6", paths)
        self.assertIn("/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", paths)
        self.assertIn("/usr/lib/felix/felix-broker", paths)

    def test_aarch64_paths_use_its_triplet_and_loader(self):
        execs = self.execs("aarch64")
        paths = {e["path"] for e in execs}
        self.assertIn("/usr/lib/aarch64-linux-gnu/libc.so.6", paths)
        self.assertIn("/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1", paths)
        self.assertFalse(any("x86" in p for p in paths))
        felixd = next(e for e in execs if e["path"] == "/usr/lib/felixd/felixd")
        self.assertIn("ld-linux-aarch64.so.1", felixd["needed"])

    def test_aarch64_ships_no_felix_server(self):
        self.assertFalse(any(e["path"].startswith("/usr/lib/felix/") for e in self.execs("aarch64")))

    def test_every_component_without_an_aarch64_build_has_no_aarch64_allowlist_entry(self):
        comps = ic.load_toml(ic.IMAGES / "components.toml")["component"]
        missing = [c["name"] for c in comps if "aarch64" not in c["artifacts"]]
        self.assertEqual(missing, ["felix"])

    def test_prune_paths_are_filled_in_for_the_target(self):
        for arch in ARCHES:
            rules = ic.for_arch(ic.load_for_target(ic.ROOT / "rootfs" / "prune.toml", target(arch)).get("prune", []), arch)
            paths = [p for r in rules for p in [*([r["path"]] if "path" in r else []), *r.get("paths", [])]]
            self.assertTrue(paths)
            self.assertTrue(all(target(arch)["triplet"] in p for p in paths), arch)


if __name__ == "__main__":
    unittest.main()
