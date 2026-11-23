"""Double explicito para testes de dominio; o produto usa evento Windows real."""

from threading import Event

from app.contatos.errors import NegocioError


class SimulatedLifetime:
    execution_id = b"f" * 16

    def __init__(self):
        self.closed, self.callbacks = Event(), []

    def subscribe(self, callback):
        self.callbacks.append(callback)
        if self.closed.is_set():
            callback()

    def require_open(self):
        if self.closed.is_set():
            raise NegocioError("aplicacao_fechando", "Fechando", 409)

    def is_open(self):
        return not self.closed.is_set()

    def close(self):
        self.closed.set()
        for callback in self.callbacks:
            callback()
