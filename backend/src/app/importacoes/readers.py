"""Leitura sem fórmulas, inferência numérica CSV ou automação do Excel."""

import csv
import io
import math
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from openpyxl import load_workbook
from openpyxl.xml import DEFUSEDXML

from app.contatos.errors import NegocioError

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20000
MAX_COLUMNS = 100


@dataclass
class Celula:
    valor: str = ""
    erro: str = ""
    aviso: str = ""


def invalido(code="arquivo_invalido"):
    return NegocioError(code, "Arquivo incompatível, corrompido ou acima dos limites permitidos.")


def proteger_zip(payload):
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > 1000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
                raise invalido("arquivo_excessivo")
            if len({i.filename for i in infos}) != len(infos):
                raise invalido()
            names = {i.filename for i in infos}
            if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                raise invalido()
            for i in infos:
                if (
                    i.flag_bits & 1
                    or i.file_size > 50 * 1024 * 1024
                    or i.file_size > max(i.compress_size, 1) * 200
                    or ".." in PurePosixPath(i.filename).parts
                    or i.filename.startswith("/")
                    or "vbaProject" in i.filename
                ):
                    raise invalido()
    except zipfile.BadZipFile, OSError, ValueError:
        raise invalido() from None


def workbook(payload):
    proteger_zip(payload)
    if not DEFUSEDXML:
        raise invalido()
    with warnings.catch_warnings(action="error"):
        return load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=False)


def inspecionar(payload, extension):
    if not payload or len(payload) > MAX_BYTES:
        raise invalido("arquivo_excessivo")
    try:
        if extension == ".xlsx":
            book = workbook(payload)
            try:
                if len(book.sheetnames) > 100:
                    raise invalido()
                return {"abas": book.sheetnames, "formato": "xlsx"}
            finally:
                book.close()
        if extension != ".csv" or b"\x00" in payload or payload.startswith(b"PK"):
            raise invalido()
        return {"abas": [], "formato": "csv"}
    except NegocioError:
        raise
    except Exception:
        raise invalido() from None


def excel_cell(cell):
    value = cell.value
    if value is None:
        return Celula()
    if cell.data_type == "f":
        return Celula(erro="formula_nao_permitida")
    if isinstance(value, str):
        return Celula(value)
    if isinstance(value, bool):
        return Celula(erro="tipo_invalido")
    if (
        isinstance(value, (int, float))
        and math.isfinite(value)
        and value == int(value)
        and abs(value) < 10**15
    ):
        # Formatos que sinalizam zeros significativos não são reconstruídos.
        fmt = cell.number_format
        if fmt not in ("General", "0", "0.00E+00", "0.00E+0"):
            return Celula(erro="telefone_numerico_ambiguo")
        return Celula(str(int(value)), aviso="celula_numerica_confira_zeros")
    return Celula(erro="tipo_ou_precisao_invalida")


def proteger_csv(content, delimiter):
    """Limita colunas antes de csv.reader alocar campos; não interpreta valores."""
    # Mesmo dialeto do reader: aspas duplas, doublequote e sem skipinitialspace.
    # A passagem usa memória constante sobre o texto já limitado pelo upload.
    state = "inicio"
    columns = 1
    for char in content:
        if state == "aspas":
            if char == '"':
                state = "apos_aspas"
            continue
        if state == "apos_aspas":
            if char == '"':
                state = "aspas"
                continue
            if char not in (delimiter, "\r", "\n"):
                raise csv.Error("CSV inválido após fechamento de aspas.")
        if char == delimiter:
            columns += 1
            if columns > MAX_COLUMNS:
                raise invalido("limites_excedidos")
            state = "inicio"
        elif char in "\r\n":
            columns = 1
            state = "inicio"
        elif char == '"' and state == "inicio":
            state = "aspas"
        else:
            state = "campo"


def linhas(payload, extension, config):
    book = None
    stream = None
    try:
        if extension == ".xlsx":
            book = workbook(payload)
            if not config.aba or config.aba not in book.sheetnames:
                raise NegocioError("aba_invalida", "Selecione uma aba válida.")
            sheet = book[config.aba]
            if sheet.max_column and sheet.max_column > MAX_COLUMNS:
                raise invalido("colunas_excessivas")
            sheet.reset_dimensions()
            iterator = ([excel_cell(c) for c in row] for row in sheet.iter_rows())
        else:
            content = payload.decode(config.codificacao, errors="strict")
            proteger_csv(content, config.delimitador)
            csv.field_size_limit(32768)
            stream = io.StringIO(content, newline="")
            iterator = (
                [Celula(v) for v in row]
                for row in csv.reader(stream, delimiter=config.delimitador, strict=True)
            )
        for index, row in enumerate(iterator, 1):
            if (
                index > MAX_ROWS + (1 if config.cabecalho else 0)
                or len(row) > MAX_COLUMNS
                or any(len(c.valor) > 32768 for c in row)
            ):
                raise invalido("limites_excedidos")
            if config.cabecalho and index <= config.cabecalho:
                continue
            yield index, row
    except NegocioError:
        raise
    except Exception:
        raise invalido() from None
    finally:
        if stream is not None:
            stream.close()
        if book:
            book.close()
