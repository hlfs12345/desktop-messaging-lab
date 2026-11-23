"""Nova instancia em processo separado, com dados exclusivamente de teste."""

import json
import secrets
import sys

from fastapi.testclient import TestClient
from helpers.runtime_lifetime import SimulatedLifetime

from app.core.config import Settings
from app.core.instance_auth import InstanceAuthorization
from app.main import create_app


def main():
    campanha, execucao = sys.argv[1:]
    auth = InstanceAuthorization(secrets.token_bytes(16), secrets.token_bytes(32))
    app = create_app(Settings.from_environment(), authorization=auth, lifetime=SimulatedLifetime())
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + auth.token}
    ) as c:
        login = c.post(
            "/auth/login", json={"login": "teste", "senha": "frase fict\u00edcia de teste 489!"}
        )
        assert login.status_code == 200
        token = login.json()["token"]
        review = app.state.execution_runtime.review(campanha, execucao, token)
        if review["permitida"]:
            app.state.execution_runtime.activate(review, token, confirmo=True)
            from time import monotonic, sleep

            limit = monotonic() + 10
            while (
                app.state.execution_runtime.snapshot(execucao)["worker_ativo"]
                and monotonic() < limit
            ):
                sleep(0.01)
        with app.state.persistence.database.engine.connect() as conn:
            attempts = conn.exec_driver_sql("SELECT estado FROM tentativas_envio").scalars().all()
        print(json.dumps({"permitida": review["permitida"], "tentativas": attempts}))


if __name__ == "__main__":
    main()
