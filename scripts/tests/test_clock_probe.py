"""rootfs/probe-clock.chroot: the build fails when the clock offset is not what was asked for."""
import os
import pathlib
import subprocess
import tempfile
import unittest

PROBE = pathlib.Path(__file__).resolve().parents[2] / "rootfs" / "probe-clock.chroot"
REAL = 1_800_000_000
# date reports the preloaded clock only when LD_PRELOAD is set, like libfaketime.
STUB = f"""#!/bin/sh
if [ -n "${{LD_PRELOAD:-}}" ]; then echo $(( {REAL} + ${{STUB_AHEAD_DAYS:-0}} * 86400 )); else echo {REAL}; fi
"""


def run(days, preload, ahead):
    with tempfile.TemporaryDirectory() as d:
        stub = pathlib.Path(d) / "date"
        stub.write_text(STUB)
        stub.chmod(0o755)
        env = {"PATH": f"{d}:/usr/bin:/bin", "STUB_AHEAD_DAYS": str(ahead)}
        if days is not None:
            env["NL_FAKETIME_DAYS"] = str(days)
        if preload:
            env["LD_PRELOAD"] = "/x"
        return subprocess.run(["sh", str(PROBE)], env=env, capture_output=True, text=True)


class Probe(unittest.TestCase):
    def test_rebuild_that_sees_the_offset_passes(self):
        r = run(400, True, 400)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("400 days ahead", r.stdout)

    def test_rebuild_at_the_real_clock_fails(self):
        r = run(400, False, 0)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not in effect", r.stderr)

    def test_rebuild_with_a_wrong_offset_fails(self):
        self.assertNotEqual(run(400, True, 30).returncode, 0)

    def test_first_build_at_the_real_clock_passes(self):
        self.assertEqual(run(None, False, 0).returncode, 0)

    def test_first_build_that_is_moved_fails(self):
        self.assertNotEqual(run(None, True, 400).returncode, 0)

    def test_script_is_executable(self):
        self.assertTrue(os.access(PROBE, os.X_OK))
