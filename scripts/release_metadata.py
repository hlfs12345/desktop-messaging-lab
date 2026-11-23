"""Fonte autoritativa de versao e configuracao nativa; somente ferramentas de build."""

import argparse
import base64
import json
import re
import struct
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def product():
    value = json.loads((ROOT / "installer/produto.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value["version"]):
        raise ValueError("Versao de produto invalida.")
    if type(value["build"]) is not int or not 0 <= value["build"] <= 65535:
        raise ValueError("Numero de build invalido.")
    if any(int(x) > 65535 for x in value["version"].split(".")):
        raise ValueError("Versao fora dos limites Windows.")
    expected = value["version"] + "+" + str(value["build"])
    frontend = (ROOT / "frontend/pubspec.yaml").read_text(encoding="utf-8")
    if not re.search(r"^version: " + re.escape(expected) + r"\s*$", frontend, re.M):
        raise ValueError("Versao Flutter diverge de installer/produto.json.")
    backend = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    if backend["project"]["version"] != value["version"]:
        raise ValueError("Versao backend diverge do produto.")
    return value


def build_profile(config):
    raw = config.read_text(encoding="utf-8")
    matches = re.findall(r'"DART_DEFINES=([^"\r\n]*)"', raw)
    if len(matches) != 1:
        raise ValueError("DART_DEFINES ausente ou ambiguo.")
    definitions = {}
    for item in matches[0].split(","):
        text = base64.b64decode(item, validate=True).decode("utf-8")
        key, separator, value = text.partition("=")
        if not separator or key in definitions:
            raise ValueError("Define ausente ou duplicado.")
        definitions[key] = value
    for key in ("EDISK_SOURCE_BACKEND", "EDISK_DISTRIBUTION"):
        if definitions.get(key, "false") not in ("true", "false"):
            raise ValueError("Define booleano invalido: " + key)
    source = definitions.get("EDISK_SOURCE_BACKEND") == "true"
    candidate = definitions.get("EDISK_DISTRIBUTION") == "true"
    if source and candidate:
        raise ValueError("Build source nao pode ser candidato.")
    return source, "candidato" if candidate else "tecnico"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["cmake", "json"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = product()
    native = value["version"] + "." + str(value["build"])
    if args.action == "json":
        print(json.dumps({**value, "native_version": native}))
    else:
        raw = args.config.read_text(encoding="utf-8")
        versions = re.findall(r'set\(FLUTTER_VERSION "([^\"]+)"', raw)
        if versions != [value["version"] + "+" + str(value["build"])]:
            raise ValueError("Versao gerada Flutter diverge da fonte autoritativa.")
        source, channel = build_profile(args.config)
        descriptor = struct.pack(
            "<8sIII6H",
            b"EDSKVAR2",
            2,
            2 if channel == "candidato" else 1,
            0 if source else 2 if channel == "candidato" else 1,
            *[int(x) for x in native.split(".")],
            0x8664,
            3,
        )
        binary = args.output.with_suffix(".bin")
        binary.write_bytes(descriptor)
        args.output.with_suffix(".rc").write_text(
            '201 RCDATA "' + binary.as_posix() + '"\n', encoding="utf-8"
        )
        args.output.write_text(
            f"set(EDISK_SOURCE {'ON' if source else 'OFF'})\n"
            f'set(EDISK_CHANNEL "{channel}")\n'
            f'set(EDISK_NATIVE_VERSION "{native}")\n',
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
