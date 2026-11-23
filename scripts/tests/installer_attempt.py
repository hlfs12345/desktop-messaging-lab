"""Logs e recibos imutaveis por tentativa dos ensaios reais Inno."""

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prepare_attempt(command):
    identity = uuid.uuid4().hex
    folder = ROOT / "build/installer-attempts" / identity
    folder.mkdir(parents=True, exist_ok=False)
    command = [str(x) for x in command if not str(x).upper().startswith("/LOG=")]
    executable = Path(command[0])
    receipt = {
        "id": identity,
        "executable": str(executable),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "log": str(folder / "inno.log"),
        "status": "started",
    }
    (folder / "started.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return [*command, "/LOG=" + str(folder / "inno.log")], folder, receipt


def finish_attempt(folder, receipt, code):
    receipt = {**receipt, "status": "completed", "exit_code": code}
    with (folder / "result.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(receipt, indent=2) + "\n")


def run_attempt(command, **options):
    command, folder, receipt = prepare_attempt(command)
    result = subprocess.run(command, **options)
    finish_attempt(folder, receipt, result.returncode)
    result.edisk_log = receipt["log"]
    return result
