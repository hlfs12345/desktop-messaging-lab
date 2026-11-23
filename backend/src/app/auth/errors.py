"""Erros públicos sem credenciais, SQL ou dados submetidos."""


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after


def unavailable() -> AuthError:
    return AuthError(
        503, "autenticacao_indisponivel", "Autenticação indisponível. Tente novamente."
    )


def invalid_credentials() -> AuthError:
    return AuthError(401, "credenciais_invalidas", "Login ou senha inválidos.")


def invalid_session() -> AuthError:
    return AuthError(401, "sessao_invalida", "Sua sessão terminou. Entre novamente.")


def configured() -> AuthError:
    return AuthError(409, "configuracao_indisponivel", "Configuração inicial indisponível.")
