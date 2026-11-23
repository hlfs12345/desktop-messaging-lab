"""Politica de producao compilada; nunca inicia backend nem abre banco."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "frontend/build/supervisor-tests/Debug/resolver_distribution_harness.exe"


class CandidatePolicyTests(unittest.TestCase):
    def test_compiled_production_ignores_inherited_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "backend").mkdir()
            (root / "backend/edisk-backend.exe").touch()
            result = subprocess.run(
                [str(HARNESS), "bundle", str(root)],
                capture_output=True,
                text=True,
                timeout=5,
                env={
                    **os.environ,
                    "EDISK_ENV": "development",
                    "EDISK_TEST_ROOT": str(root),
                    "EDISK_DEV_ROOT": "Z:/nao",
                },
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("bundled/production", result.stdout)
            self.assertNotIn("EDISK_TEST_ROOT=", result.stdout)
            self.assertNotIn("EDISK_DEV_ROOT=", result.stdout)
            for option in (
                "--edisk-producao",
                "--edisk-test-root=" + str(root),
                "--edisk-foo",
            ):
                self.assertNotEqual(
                    subprocess.run(
                        [str(HARNESS), "bundle", str(root), option],
                        capture_output=True,
                        timeout=5,
                    ).returncode,
                    0,
                )
            self.assertNotEqual(
                subprocess.run(
                    [str(HARNESS), "source", str(root)], capture_output=True, timeout=5
                ).returncode,
                0,
            )


if __name__ == "__main__":
    unittest.main()
