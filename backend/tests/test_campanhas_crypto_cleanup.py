"""Falhas nativas simuladas; vetores reais permanecem em test_campanhas_crypto."""

import ctypes as C

import pytest

from app.campanhas import crypto
from app.contatos.errors import NegocioError

FAIL = 0xC000000D
INVALID_HANDLE = 0xC0000008


class Native:
    def __init__(self, destroy=(), close=()):
        self.plans = {"destroy": list(destroy), "close": list(close)}
        self.calls = []
        self.live = {"key": True, "alg": True}

    def release(self, name):
        self.calls.append(name)
        plan = self.plans[name]
        result = plan.pop(0) if plan else 0
        if isinstance(result, Exception):
            raise result
        if result in (0, INVALID_HANDLE):
            self.live["key" if name == "destroy" else "alg"] = False
        return result

    def BCryptDestroyKey(self, key):
        assert key.value == 11
        return self.release("destroy")

    def BCryptCloseAlgorithmProvider(self, alg, flags):
        assert alg.value == 12 and flags == 0
        return self.release("close")


def holder(native):
    g = crypto.GCM.__new__(crypto.GCM)
    g.lib, g.key, g.alg = native, C.c_void_p(11), C.c_void_p(12)
    g._fechado = False
    return g


@pytest.mark.parametrize("failure", ["destroy", "close"])
def test_retorno_verificado_e_limpeza_idempotente(failure):
    native = Native(**{failure: [FAIL, 0]})
    g = holder(native)
    with pytest.raises(NegocioError) as error:
        g.close()
    assert error.value.codigo == "conteudo_indisponivel"
    assert not any(native.live.values())
    assert native.calls.count(failure) == 2
    calls = list(native.calls)
    g.close()
    g.__del__()
    assert native.calls == calls


@pytest.mark.parametrize("failure", ["destroy", "close"])
def test_tenta_outros_recursos_e_retenta_somente_pendentes(failure):
    native = Native(**{failure: [FAIL, FAIL]})
    g = holder(native)
    with pytest.raises(NegocioError):
        g.close()
    assert "destroy" in native.calls and "close" in native.calls
    other = "close" if failure == "destroy" else "destroy"
    count = native.calls.count(other)
    with pytest.raises(NegocioError):
        g.encrypt(bytes(12), b"nao executar", b"")
    g.close()
    assert not any(native.live.values())
    assert native.calls.count(other) == count


def test_excecao_original_preservada_e_ambos_recursos_tentados():
    native = Native(destroy=[OSError("falha simulada"), 0], close=[FAIL, 0])
    g = holder(native)
    primary = RuntimeError("operacao principal")
    with pytest.raises(RuntimeError) as error, g:
        raise primary
    assert error.value is primary
    assert not any(native.live.values())


def test_handle_invalido_nao_e_liberado_duas_vezes():
    native = Native(destroy=[INVALID_HANDLE])
    g = holder(native)
    with pytest.raises(NegocioError):
        g.close()
    assert native.calls.count("destroy") == 1
    calls = list(native.calls)
    g.close()
    assert native.calls == calls


def test_falha_na_construcao_preserva_erro_e_limpa_algoritmo(monkeypatch):
    primary = RuntimeError("configuracao principal")

    class Constructor(Native):
        def BCryptOpenAlgorithmProvider(self, target, *_):
            target._obj.value = 12
            return 0

        def BCryptSetProperty(self, *_):
            raise primary

    native = Constructor(close=[FAIL, 0])
    monkeypatch.setattr(crypto, "api", lambda: native)
    with pytest.raises(RuntimeError) as error:
        crypto.GCM(bytes(32))
    assert error.value is primary
    assert native.calls == ["close", "close"]
    assert not native.live["alg"]
