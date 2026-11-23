"""Fecha inventario e confirma a politica incorporada depois dos checks do bundle."""

import json
import sys
from pathlib import Path

from installer_windows import listing
from native_variant import require_variant
from release_metadata import product

root = Path(sys.argv[1]).absolute()
channel = sys.argv[2]
version = product()

require_variant(root / "edisk.exe", channel, version["version"] + "." + str(version["build"]))
files = listing(root)
if "content-inventory.json" in files or "manifest-sha256.json" in files:
    raise ValueError("Inventario requer bundle ainda nao fechado.")
(root / "content-inventory.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
