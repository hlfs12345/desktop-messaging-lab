"""Contrato PE RCDATA 201: leitura estrutural, sem executar o binario."""

import struct

import pefile


def read_variant(path):
    pe = pefile.PE(str(path))
    try:
        entries = [
            language.data.struct
            for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries
            if kind.id == 10
            for resource in kind.directory.entries
            if resource.id == 201
            for language in resource.directory.entries
        ]
        if len(entries) != 1 or entries[0].Size != 32:
            raise ValueError("Recurso de variante ausente ou ambiguo.")
        data = pe.get_data(entries[0].OffsetToData, entries[0].Size)
        magic, schema, channel, profile, *words = struct.unpack("<8sIII6H", data)
        if (
            magic != b"EDSKVAR2"
            or schema != 2
            or channel not in (1, 2)
            or profile not in (0, 1, 2)
            or words[4:] != [0x8664, 3]
        ):
            raise ValueError("Contrato nativo invalido.")
        return dict(
            schema=schema,
            channel={1: "tecnico", 2: "candidato"}[channel],
            profile={0: "source", 1: "bundled/test", 2: "bundled/production"}[profile],
            version=".".join(map(str, words[:4])),
            architecture="x64",
            installation_protocol=3,
        )
    except (AttributeError, struct.error) as error:
        raise ValueError("Runner sem contrato PE.") from error
    finally:
        pe.close()


def require_variant(path, channel, version):
    value = read_variant(path)
    expected_profile = "bundled/production" if channel == "candidato" else "bundled/test"
    if (
        value["channel"] != channel
        or value["profile"] != expected_profile
        or value["version"] != version
    ):
        raise ValueError("Variante nativa incompativel com canal, perfil ou versao.")
    return value
