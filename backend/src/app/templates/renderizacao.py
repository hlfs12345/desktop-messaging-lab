"""Substituição literal em uma passagem; nunca interpreta código ou valores."""

from app.contatos.errors import NegocioError


def variaveis(conteudo: str) -> list[str]:
    restante = conteudo.replace("{{nome}}", "")
    if not conteudo.strip() or any(c in restante for c in ("{", "}", "\x00")):
        raise NegocioError("template_invalido", "Use texto e somente a variável {{nome}}.", 422)
    return ["nome"] if "{{nome}}" in conteudo else []


def previa(conteudo: str, valores: dict[str, str]) -> dict:
    required = variaveis(conteudo)
    if "nome" in required and not valores.get("nome", "").strip():
        raise NegocioError("valor_ausente", "Informe um nome fictício para a prévia.", 422)
    return {
        "conteudo_renderizado": conteudo.replace("{{nome}}", valores.get("nome", "")),
        "variaveis": required,
        "validacao": "somente_conteudo_local",
    }
