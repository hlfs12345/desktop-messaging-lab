"""Bot API somente de leitura; nunca expõe URL, credencial ou corpo em erros."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPSConnection
from socket import SHUT_RDWR
from threading import Event, Lock

from app.contatos.errors import NegocioError


@dataclass(frozen=True)
class ResultadoEnvioTelegram:
    estado: str
    codigo: str
    message_id: int | None = None
    retry_after: int | None = None


def _get_me(token):
    # Host fixo, TLS padrão, sem redirects/proxy ou logs da URL (que contém o token).
    connection = HTTPSConnection("api.telegram.org", timeout=8)
    try:
        connection.request("GET", f"/bot{token}/getMe")
        response = connection.getresponse()
        return response.status, response.read(65537)
    finally:
        connection.close()


class Telegram:
    def __init__(self, transporte=None):
        self.transporte = transporte or _get_me
        self._lock = Lock()
        self._cancelado = Event()
        self._connection = None
        self._socket = None

    def verificar(self, token, *, recepcao=False):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise NegocioError("telegram_credencial_invalida", "Credencial Telegram inválida.")
        # Não propagar exceção original: pode conter URL, token ou corpo remoto.
        falha = None
        try:
            status, raw = (
                self._consultar(token, "getMe", {}) if recepcao else self.transporte(token)
            )
        except TimeoutError:
            falha = NegocioError("telegram_timeout", "Telegram não respondeu no prazo.", 504)
        except Exception:
            falha = NegocioError(
                "telegram_comunicacao", "Não foi possível comunicar com o Telegram.", 502
            )
        if falha:
            raise falha
        if status in (401, 404):
            raise NegocioError("telegram_credencial_invalida", "Credencial Telegram inválida.")
        if status != 200:
            raise NegocioError("telegram_http", "Telegram indisponível. Tente novamente.", 502)
        try:
            if len(raw) > 65536:
                raise ValueError
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
                raise ValueError
            bot = data["result"]
            identifier, nome, username = bot["id"], bot["first_name"], bot.get("username")
            if (
                data["ok"] is not True
                or bot["is_bot"] is not True
                or type(identifier) is not int
                or not 0 < identifier < 2**52
                or not isinstance(nome, str)
                or not nome.strip()
                or len(nome) > 256
                or token in nome
                or any(ord(c) < 32 for c in nome)
                or (
                    username is not None
                    and (
                        not isinstance(username, str)
                        or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username)
                    )
                )
            ):
                raise ValueError
            nome.encode("utf-8")
        except ValueError, TypeError, KeyError, UnicodeError:
            falha = NegocioError("telegram_resposta", "Resposta inesperada do Telegram.", 502)
        if falha:
            raise falha
        return {"id": identifier, "nome": nome, "username": username}

    def cancelar(self):
        self._cancelado.set()
        # O socket pode estar em leitura por outra thread. Só interromper o socket;
        # o proprietário fecha a conexão no finally, sem disputar locks de buffers.
        connection = self._connection
        sock = self._socket or (connection.sock if connection is not None else None)
        if sock is not None:
            try:
                sock.shutdown(SHUT_RDWR)
            except OSError:
                pass

    def _consultar(self, token, metodo, params):
        if self._cancelado.is_set():
            raise NegocioError("telegram_encerrada", "Recepção encerrada.")
        connection = HTTPSConnection("api.telegram.org", timeout=5)
        with self._lock:
            if self._cancelado.is_set():
                connection.close()
                raise NegocioError("telegram_encerrada", "Recepção encerrada.")
            self._connection = connection
        falha = None
        try:
            connection.request(
                "POST",
                f"/bot{token}/{metodo}",
                body=json.dumps(params),
                headers={"Content-Type": "application/json"},
            )
            with self._lock:
                self._socket = connection.sock
                if self._cancelado.is_set():
                    raise OSError
            response = connection.getresponse()
            raw = response.read(2_097_153)
            status = response.status
        except TimeoutError:
            falha = NegocioError("telegram_timeout", "Telegram não respondeu no prazo.", 504)
        except Exception:
            falha = NegocioError("telegram_comunicacao", "Falha de comunicação Telegram.", 502)
        finally:
            with self._lock:
                self._connection = None
                self._socket = None
                connection.close()
        if falha:
            raise falha
        if len(raw) > 2_097_152:
            raise NegocioError("telegram_resposta", "Resposta Telegram excessiva.", 502)
        return status, raw

    def atualizacoes(self, token, offset, username):
        params = {"timeout": 1, "limit": 100, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        status, raw = self._consultar(token, "getUpdates", params)
        if status == 409:
            raise NegocioError("telegram_conflito", "Webhook ou outro consumidor ativo.", 409)
        if status in (401, 404):
            raise NegocioError("telegram_credencial_invalida", "Credencial Telegram inválida.")
        if status != 200:
            raise NegocioError("telegram_http", "Telegram indisponível.", 502)
        invalid = False
        try:
            data = json.loads(raw)
            updates = data["result"]
            if data["ok"] is not True or not isinstance(updates, list) or len(updates) > 100:
                raise ValueError
            normalized = [self._normalizar(item, username) for item in updates]
            ids = [item["update_id"] for item in normalized]
            if ids != sorted(ids):
                raise ValueError
        except ValueError, TypeError, KeyError, OverflowError, OSError:
            invalid = True
        if invalid:
            raise NegocioError("telegram_resposta", "Resposta Telegram inválida.", 502)
        return normalized

    def responder(self, token, chat_id, texto):
        if (
            not re.fullmatch(r"[1-9][0-9]{0,15}", chat_id)
            or not isinstance(texto, str)
            or not texto.strip()
            or len(texto) > 4096
        ):
            raise NegocioError("telegram_resposta", "Resposta Telegram inválida.")
        status, raw = self._consultar(
            token,
            "sendMessage",
            {"chat_id": int(chat_id), "text": texto},
        )
        if status in (401, 404):
            raise NegocioError("telegram_credencial_invalida", "Credencial Telegram inválida.")
        if status != 200:
            raise NegocioError("telegram_http", "Telegram indisponível.", 502)
        try:
            data = json.loads(raw)
            message = data["result"]
            if (
                data["ok"] is not True
                or not isinstance(message, dict)
                or type(message.get("message_id")) is not int
                or message.get("chat", {}).get("id") != int(chat_id)
            ):
                raise ValueError
        except ValueError, TypeError, KeyError:
            raise NegocioError("telegram_resposta", "Resposta Telegram inválida.", 502) from None

    def enviar(self, token, chat_id, texto):
        """Uma tentativa; nunca repete nem expõe corpo, token ou resposta remota."""
        if (
            not re.fullmatch(r"[1-9][0-9]{0,15}", chat_id)
            or not isinstance(texto, str)
            or not texto.strip()
            or len(texto) > 4096
        ):
            return ResultadoEnvioTelegram("falhou", "entrada_invalida")
        try:
            status, raw = self._consultar(
                token,
                "sendMessage",
                {"chat_id": int(chat_id), "text": texto},
            )
        except NegocioError:
            return ResultadoEnvioTelegram("desconhecida", "sem_confirmacao")
        if status == 429:
            retry_after = None
            try:
                data = json.loads(raw)
                value = data.get("parameters", {}).get("retry_after")
                if type(value) is int and 1 <= value <= 86400:
                    retry_after = value
            except ValueError, TypeError:
                pass
            return ResultadoEnvioTelegram(
                "falhou", "limite_telegram", retry_after=retry_after
            )
        if status in (401, 404):
            return ResultadoEnvioTelegram("falhou", "credencial_invalida")
        if status in (400, 403):
            return ResultadoEnvioTelegram("falhou", "destino_rejeitado")
        if status != 200:
            return ResultadoEnvioTelegram("desconhecida", "sem_confirmacao")
        try:
            data = json.loads(raw)
            message = data["result"]
            message_id = message["message_id"]
            if (
                data["ok"] is not True
                or type(message_id) is not int
                or message_id <= 0
                or message.get("chat", {}).get("id") != int(chat_id)
            ):
                raise ValueError
        except ValueError, TypeError, KeyError:
            return ResultadoEnvioTelegram("desconhecida", "sem_confirmacao")
        return ResultadoEnvioTelegram("confirmada", "aceita", message_id=message_id)

    @staticmethod
    def _normalizar(update, username):
        if not isinstance(update, dict):
            raise ValueError
        identifier = update["update_id"]
        if type(identifier) is not int or not 0 <= identifier < 2**63 - 1:
            raise ValueError
        result = {"update_id": identifier, "resultado": "ignorada"}
        msg = update.get("message")
        if not isinstance(msg, dict) or "forward_origin" in msg:
            return result
        chat, sender, texto = msg.get("chat"), msg.get("from"), msg.get("text")
        if not isinstance(chat, dict) or not isinstance(sender, dict) or not isinstance(texto, str):
            return result
        user_id, chat_id = sender.get("id"), chat.get("id")
        if (
            chat.get("type") != "private"
            or sender.get("is_bot") is not False
            or type(user_id) is not int
            or type(chat_id) is not int
            or user_id != chat_id
            or not 0 < user_id < 2**52
        ):
            return result
        command = re.fullmatch(
            r"/(start|stop|reativar|aceitar)(?:@([A-Za-z0-9_]{1,32}))?(?:[ \t]+([^\r\n]*))?",
            texto,
        )
        if command is None or (
            command[2] is not None and command[2].lower() != (username or "").lower()
        ):
            return result
        argumento = (command[3] or "").strip()
        if command[1] == "aceitar" and argumento != "promocoes":
            return result
        date = msg.get("date")
        if type(date) is not int or date < 0:
            raise ValueError
        return {
            **result,
            "resultado": {
                "start": "iniciou",
                "stop": "revogou",
                "reativar": "iniciou",
                "aceitar": "autorizou",
            }[command[1]],
            "reativacao": command[1] == "reativar",
            "usuario_externo_id": str(user_id),
            "chat_externo_id": str(chat_id),
            "ocorrido_em": datetime.fromtimestamp(date, UTC),
        }
