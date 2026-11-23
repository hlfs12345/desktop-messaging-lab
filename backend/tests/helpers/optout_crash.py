"""Queda abrupta em fronteiras reais do opt-out; uso exclusivo de fixtures."""

import json
import os
import secrets
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from helpers.runtime_lifetime import SimulatedLifetime

from app.consentimentos.schemas import EventoEntrada
from app.core.config import Settings
from app.core.instance_auth import InstanceAuthorization
from app.main import create_app


def cair(*_args, **_kwargs):
    os._exit(73)


def main():
    phase, source = sys.argv[1:]
    pedido = json.loads(Path(source).read_text(encoding="utf-8"))
    auth = InstanceAuthorization(secrets.token_bytes(16), secrets.token_bytes(32))
    app = create_app(Settings.from_environment(), authorization=auth, lifetime=SimulatedLifetime())
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + auth.token}
    ) as c:
        result = c.post(
            "/auth/login", json={"login": "teste", "senha": "frase fict\u00edcia de teste 489!"}
        )
        assert result.status_code == 200
        token = result.json()["token"]
        svc = app.state.consentimentos
        guard = app.state.persistence.database.suspensoes_consentimento
        if phase == "antes_intencao_sqlite":
            guard._espelhar = cair
        elif phase == "depois_intencao":
            svc._registrar = cair
        elif phase == "depois_optout":
            guard.confirmar = cair
        else:
            raise ValueError("Fronteira de teste desconhecida")
        svc.registrar(pedido["contato_id"], EventoEntrada.model_validate(pedido["data"]), token)
        raise AssertionError("Nao caiu na fronteira de teste")


if __name__ == "__main__":
    main()
