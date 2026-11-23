"""Resolver nativo real, independente de Flutter ou envio HTTP."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "frontend/build/supervisor-tests/Debug/resolver_harness.exe"


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edisk-resolver-")
        self.root = Path(self.temp.name)
        (self.root / "backend").mkdir()
        (self.root / "backend/edisk-backend.exe").touch()

    def tearDown(self):
        self.temp.cleanup()

    def resolve(self, *arguments, source=False, **environment):
        result = subprocess.run(
            [str(HARNESS), "source" if source else "bundle", str(self.root), *arguments],
            capture_output=True,
            text=True,
            env={**os.environ, **environment},
            timeout=5,
        )
        return result.returncode, result.stdout

    def test_bundle_ignores_inherited_development_and_production(self):
        code, output = self.resolve(
            EDISK_DEV_ROOT=str(ROOT),
            EDISK_ENV="production",
            EDISK_TEST_ROOT=str(ROOT),
            PYTHONPATH="malicioso",
        )
        self.assertEqual(code, 0)
        self.assertIn("bundled/test", output)
        self.assertIn("eDisk-validacao-tecnica", output)
        self.assertNotIn(".venv", output)
        self.assertNotIn("PYTHONPATH", output)
        self.assertNotIn("EDISK_DEV_ROOT", output)

    def test_technical_profile_never_accepts_production(self):
        self.assertNotEqual(self.resolve("--edisk-producao")[0], 0)

    def test_installed_layout_is_case_insensitive(self):
        previous = self.root
        try:
            self.root = previous / "RELEASES" / "0.1.0.3-fixture"
            (self.root / "backend").mkdir(parents=True)
            (self.root / "backend/edisk-backend.exe").touch()
            code, output = self.resolve()
            self.assertEqual(code, 0)
            self.assertIn("eDisk-validacao-instalador", output)
            self.assertNotIn("eDisk-validacao-tecnica", output)
            self.assertNotEqual(self.resolve("--edisk-producao")[0], 0)
        finally:
            self.root = previous

    def test_test_root_explicit(self):
        code, output = self.resolve("--edisk-test-root=" + str(self.root / "dados"))
        self.assertEqual(code, 0)
        self.assertIn("EDISK_TEST_ROOT=" + str(self.root / "dados"), output)

    def test_invalid_and_conflicting_options(self):
        for args in [
            ("--edisk-test-root=relative",),
            ("--edisk-invalido",),
            ("--edisk-producao", "--edisk-producao"),
            ("--edisk-producao", "--edisk-test-root=" + str(self.root)),
        ]:
            self.assertNotEqual(self.resolve(*args)[0], 0)

    def test_missing_backend_no_fallback(self):
        (self.root / "backend/edisk-backend.exe").unlink()
        code, output = self.resolve(EDISK_DEV_ROOT=str(ROOT))
        self.assertNotEqual(code, 0)
        self.assertIn("backend_missing", output)

    def test_source_requires_explicit_root_and_never_production(self):
        self.assertEqual(
            self.resolve(source=True, EDISK_DEV_ROOT=str(ROOT), EDISK_ENV="development")[0], 0
        )
        self.assertNotEqual(
            self.resolve(source=True, EDISK_DEV_ROOT=str(ROOT), EDISK_ENV="production")[0], 0
        )


if __name__ == "__main__":
    unittest.main()
