"""Vetores conhecidos e rejei??o autenticada com CNG real."""

import os

import pytest

from app.campanhas.crypto import GCM
from app.contatos.errors import NegocioError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Exige CNG Windows real")


@pytest.mark.parametrize(
    "plain,cipher",
    [
        ("", "530f8afbc74536b9a963b4f1c4cb738b"),
        ("00" * 16, "cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919"),
    ],
)
def test_vetor_aes256_gcm(plain, cipher):
    with GCM(bytes(32)) as g:
        assert g.encrypt(bytes(12), bytes.fromhex(plain), b"").hex() == cipher
        assert g.decrypt(bytes(12), bytes.fromhex(cipher), b"") == bytes.fromhex(plain)


@pytest.mark.parametrize("change", ["nonce", "tag", "aad", "key"])
def test_autenticacao(change):
    key, nonce, aad = os.urandom(32), os.urandom(12), b"contexto"
    with GCM(key) as g:
        cipher = g.encrypt(nonce, b"conteudo privado", aad)
    with GCM(os.urandom(32) if change == "key" else key) as g:
        with pytest.raises(NegocioError):
            g.decrypt(
                os.urandom(12) if change == "nonce" else nonce,
                cipher[:-1] + bytes([cipher[-1] ^ 1]) if change == "tag" else cipher,
                b"outro" if change == "aad" else aad,
            )
