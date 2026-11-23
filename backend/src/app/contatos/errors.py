"""Erros fixos sem valores pessoais."""


class NegocioError(Exception):
    def __init__(self, codigo: str, mensagem: str, status: int = 422):
        super().__init__(mensagem)
        self.codigo, self.status = codigo, status


def conflito():
    return NegocioError(
        "conflito", "Os dados mudaram. Atualize a consulta e revise novamente.", 409
    )


def ausente():
    return NegocioError("nao_encontrado", "Registro não encontrado.", 404)


def banco_ocupado():
    return NegocioError(
        "banco_ocupado",
        "Banco ocupado por outra conexão. Finalize a consulta externa e atualize os dados.",
        503,
    )
