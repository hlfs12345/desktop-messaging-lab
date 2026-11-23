"""Limites por processo; nenhuma espera segura usa o relógio de parede."""

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.auth.errors import AuthError


def limited(seconds: int = 1) -> AuthError:
    return AuthError(429, "tentativas_limitadas", "Aguarde antes de tentar novamente.", seconds)


@dataclass
class Bucket:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0
    strikes: int = 0
    touched: float = 0


class Attempts:
    def __init__(self, clock: Callable[[], float] = time.monotonic, capacity: int = 1024):
        self.clock = clock
        self.capacity = capacity
        self._secret = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._global: deque[float] = deque()
        self._buckets: dict[bytes, Bucket] = {}

    def reserve(self, login: str) -> bytes:
        key = hmac.digest(self._secret, login.encode("utf-8"), hashlib.sha256)
        with self._lock:
            now = self.clock()
            while self._global and self._global[0] <= now - 60:
                self._global.popleft()
            if len(self._global) >= 20:
                raise limited(max(1, math.ceil(60 - (now - self._global[0]))))
            self._global.append(now)
            for old in list(self._buckets):
                bucket = self._buckets[old]
                retention = 1800 if bucket.strikes else 300
                if now - bucket.touched >= retention and now >= bucket.blocked_until:
                    del self._buckets[old]
            if key not in self._buckets:
                if len(self._buckets) >= self.capacity:
                    raise limited(60)
                self._buckets[key] = Bucket(touched=now)
            bucket = self._buckets[key]
            bucket.touched = now
            if now < bucket.blocked_until:
                raise limited(max(1, math.ceil(bucket.blocked_until - now)))
            return key

    def failure(self, key: bytes) -> None:
        with self._lock:
            now = self.clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                return
            bucket.touched = now
            while bucket.failures and bucket.failures[0] <= now - 300:
                bucket.failures.popleft()
            bucket.failures.append(now)
            if len(bucket.failures) >= 5 or bucket.strikes:
                bucket.strikes = min(bucket.strikes + 1, 5)
                bucket.blocked_until = now + min(60 * 2 ** (bucket.strikes - 1), 900)

    def check(self, key: bytes) -> None:
        """Uma falha anterior pode bloquear o login enquanto aguarda o gate."""
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is not None and self.clock() < bucket.blocked_until:
                raise limited(max(1, math.ceil(bucket.blocked_until - self.clock())))

    def success(self, key: bytes) -> None:
        with self._lock:
            if key in self._buckets:
                self._buckets[key] = Bucket(touched=self.clock())


class CryptoGate:
    """Uma execução e um aguardante; a fila tem espera máxima de dois segundos."""

    def __init__(self) -> None:
        self._slots = threading.BoundedSemaphore(2)
        self._running = threading.Lock()

    @contextmanager
    def enter(self) -> Iterator[None]:
        if not self._slots.acquire(blocking=False):
            raise limited()
        acquired = False
        try:
            acquired = self._running.acquire(timeout=2)
            if not acquired:
                raise limited()
            yield
        finally:
            if acquired:
                self._running.release()
            self._slots.release()
