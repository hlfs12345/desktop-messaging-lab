"""Argon2id explícito, com limites defensivos para hashes persistidos."""

import secrets

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import Argon2Error, InvalidHashError
from argon2.low_level import Type

from app.auth.errors import AuthError

# Recurso local original do eDisk, sob a licença proprietária do projeto.
# Lista curta de exemplos óbvios, sem incorporação de bases de terceiros.
COMMON_PASSWORDS = frozenset(
    {
        "123456789012345",
        "1234567890123456",
        "12345678901234567890",
        "passwordpassword",
        "password123456789",
        "senha12345678900",
        "administrador123",
        "qwertyuiopasdfgh",
        "aaaaaaaaaaaaaaa",
    }
)


class Passwords:
    def __init__(self) -> None:
        self.hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            salt_len=16,
            hash_len=32,
            type=Type.ID,
        )
        self._dummy = self.hasher.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        if password.casefold() in COMMON_PASSWORDS or len(set(password)) == 1:
            raise AuthError(422, "senha_comum", "Escolha uma senha menos previsível.")
        return self.hasher.hash(password)

    def dummy(self, password: str) -> None:
        try:
            self.hasher.verify(self._dummy, password)
        except Argon2Error:
            pass

    def verify(self, stored: str, password: str) -> bool:
        try:
            if len(stored) > 512:
                raise InvalidHashError
            params = extract_parameters(stored)
            if (
                params.type != Type.ID
                or params.version != 19
                or not 8192 <= params.memory_cost <= 131072
                or not 1 <= params.time_cost <= 6
                or not 1 <= params.parallelism <= 4
                or not 8 <= params.salt_len <= 32
                or not 16 <= params.hash_len <= 64
            ):
                raise InvalidHashError
        except InvalidHashError, ValueError:
            self.dummy(password)
            return False
        try:
            return self.hasher.verify(stored, password)
        except Argon2Error:
            return False

    def needs_rehash(self, stored: str) -> bool:
        return self.hasher.check_needs_rehash(stored)
