# Dependências do backend - entregas 1 a 6

## Atualização da Entrega 6 — 29/10/2026

Única dependência direta nova: argon2-cffi. `uv sync --no-python-downloads`
preservou as versões anteriores e acrescentou a cadeia abaixo, travada no lock.
Os pacotes foram instalados como wheels; não foi necessário compilar terceiros
nem instalar runtime global. CPython 3.14.10 Windows x64 foi exercitado com
hashing real, HTTP e subprocessos. Não foi criado empacotador/instalador.

| Pacote | Versão | Uso | Licença declarada | Wheel instalado |
|---|---|---|---|---|
| argon2-cffi | 25.1.0 | API de alto nível Argon2id | MIT | py3-none-any |
| argon2-cffi-bindings | 26.1.0 | Extensão com Argon2 nativo incorporado | MIT; conferir código incorporado abaixo | cp310-abi3-win_amd64 |
| cffi | 2.1.1 | FFI nativa transitiva | MIT-0; exceções internas abaixo | cp314-cp314-win_amd64 |
| pycparser | 3.12 | Dependência transitiva CFFI | BSD-3-Clause | py3-none-any |

Evidências locais: METADATA, WHEEL, RECORD e arquivos licenses/LICENSE nas quatro
distribuições instaladas; `uv tree --locked --no-dev`, `uv lock --check` e
`uv pip check` (34 distribuições compatíveis, 35 entradas no lock universal).
Ferramentas scikit-build-core/setuptools-scm pertencem ao build upstream, não
foram adicionadas às dependências de execução do Desktop Messaging Lab.

Fontes primárias: [argon2-cffi 25.1.0](https://pypi.org/project/argon2-cffi/25.1.0/),
[bindings 26.1.0](https://github.com/hynek/argon2-cffi-bindings/blob/26.1.0/pyproject.toml),
[CFFI 2.1.1](https://github.com/python-cffi/cffi/blob/v2.1.1/LICENSE) e
[pycparser 3.12](https://pypi.org/project/pycparser/3.12/).

### Código nativo e obrigações além do METADATA

O [README dos bindings 26.1.0](https://github.com/hynek/argon2-cffi-bindings/blob/26.1.0/README.md)
identifica o Argon2 incorporado no commit
`f57e61e19229e23c4445b85494dbf7c07de721cb`, incluindo rotinas de codificação de
Thomas Pornin e BLAKE2 de Samuel Neves, sob CC0. O
[LICENSE desse commit](https://github.com/P-H-C/phc-winner-argon2/blob/f57e61e19229e23c4445b85494dbf7c07de721cb/LICENSE)
oferece CC0-1.0 ou Apache-2.0; conservar a identificação e o texto aplicável
no futuro conjunto distribuído. Os bindings têm licença MIT própria.

CFFI declara MIT-0, mas seu [setup Windows x64](https://github.com/python-cffi/cffi/blob/v2.1.1/setup.py)
incorpora `libffi_x86_x64`. Seu
[LICENSE MIT](https://github.com/python-cffi/cffi/blob/v2.1.1/src/c/libffi_x86_x64/LICENSE)
exige preservar copyright Red Hat, permissão e disclaimer. Não classificar todo
o binário CFFI apenas como MIT-0. O wheel instalado traz o LICENSE principal;
a composição dos avisos nativos deve ser preservada/conferida no empacotamento.

Triagem permite o desenvolvimento proprietário: não foi identificado copyleft
nessa cadeia. Preservar MIT/BSD e demais avisos de terceiros separadamente,
sem alterar a licença proprietária do Desktop Messaging Lab. Distribuição do futuro executável,
runtime Python e instalador continua dependente da auditoria dos artefatos reais.

### Custo e recurso local de senhas

Parâmetros explícitos 65536 KiB, t=3, p=4, salt=16, hash=32 e Argon2id.
Dez verificações locais: mediana 39,63 ms, máximo 40,94 ms, sem promessa para
outro hardware. Medição com perf_counter, senhas aleatórias somente em memória.
Rever parâmetros com benchmark em atualizações, sem redução silenciosa.

A lista curta COMMON_PASSWORDS em app/auth/passwords.py foi escrita no projeto,
sob sua licença proprietária, com exemplos óbvios e rejeição de um único
caractere repetido. Não incorpora listas de terceiros nem base de vazamentos;
não pretende detectar toda senha comprometida. Normalização NFC usa unicodedata
da biblioteca padrão. Tokens, SHA-256/HMAC e limites também usam biblioteca padrão.

O estado vigente está em `../docs/CONTEXTO_ATUAL.md`. Registros abaixo preservam
somente decisões de dependências e licenças.

## Atualização da Entrega 5 — 29/10/2026

Nenhum pacote novo ou versão alterada. Autorização por instância e protocolo
privado usam bibliotecas padrão (secrets/hmac via compare_digest, struct, socket,
threading, asyncio e msvcrt). O segredo é gerado no supervisor Windows e recebido
por pipe; não é configuração de ambiente. Uvicorn Server/Config e socket fornecido
são usados na versão travada, com integração real e sem alterar SQLAlchemy/Alembic.
O uso de started/should_exit fica encapsulado no launcher e exige regressão em
atualizações de Uvicorn. Não há empacotador, novo runtime ou aprovação de binários.

Os registros abaixo preservam a origem e a triagem de licenças da cadeia existente.

Verificação em **29/10/2026**, Windows x64, CPython **3.14.10**, uv **0.13.6**.
As versões abaixo correspondem ao `uv.lock` e aos metadados dos pacotes instalados.
Os intervalos no `pyproject.toml` permitem manutenção deliberada; executar com
`--locked` preserva a resolução validada. `uv_build` tem versão exata no build-system,
pois dependências de build não são travadas pelo lock da aplicação.

## Dependências diretas

| Pacote | Versão validada | Uso e justificativa | Licença declarada |
|---|---|---|---|
| FastAPI | 0.162.0 | Aplicação HTTP e contrato do health; stack aprovada | MIT |
| Uvicorn | 0.53.0 | Servidor ASGI local; sem extras, usando asyncio/h11 | BSD-3-Clause |
| SQLAlchemy | 2.0.55 | Persistência síncrona SQLite, transações e sessões, sem extras | MIT |
| Alembic | 1.20.1 | Inicialização técnica e evolução versionada do esquema no startup | MIT |
| HTTPX2 | 2.16.0 | Somente testes: TestClient do Starlette e chamadas ao subprocesso local | BSD-3-Clause |
| pytest | 9.2.0 | Somente desenvolvimento: testes do endpoint e da inicialização | MIT |
| Ruff | 0.17.3 | Somente desenvolvimento: lint e formatação | MIT |
| uv_build | 0.13.6 | Somente build/instalação editável do pacote em src/app; integração com uv | MIT OR Apache-2.0 |

Fontes primárias: [FastAPI](https://pypi.org/project/fastapi/0.162.0/),
[Uvicorn](https://pypi.org/project/uvicorn/0.53.0/),
[HTTPX2](https://pypi.org/project/httpx2/2.16.0/),
[pytest](https://pypi.org/project/pytest/9.2.0/),
[Ruff](https://pypi.org/project/ruff/0.17.3/) e
[uv_build](https://pypi.org/project/uv-build/0.13.6/).
A [documentação de uv_build](https://docs.astral.sh/uv/concepts/build-backend/)
descreve a instalação do pacote puro Python em layout src.

O TestClient instalado do Starlette 1.10.0 recomenda HTTPX2 e emite aviso de
depreciação com HTTPX. Por isso os testes usam apenas HTTPX2, sem manter os dois
clientes e sem ignorar esse aviso. Python 3.14 foi validado pelo sync, importação,
21 testes (avisos tratados como erros) e execução HTTP real; o wheel instalado
de pydantic-core é `cp314-cp314-win_amd64`. A persistência foi validada na Entrega 3,
conforme registro abaixo. Naquele registro, Argon2id e empacotadores ainda não
haviam sido testados; Argon2id foi validado na Entrega 6, acima.

## Cadeia transitiva efetivamente instalada no Windows

| Pacote | Versão | Contexto | Licença declarada |
|---|---|---|---|
| annotated-doc | 0.0.5 | Execução / FastAPI | MIT |
| annotated-types | 0.8.0 | Execução / Pydantic | MIT |
| anyio | 4.15.1 | Execução / Starlette; também testes | MIT |
| click | 8.5.0 | Execução / Uvicorn | BSD-3-Clause |
| greenlet | 3.5.6 | Execução / dependência do SQLAlchemy nesta arquitetura Windows | MIT AND PSF-2.0 |
| Mako | 1.4.3 | Execução / dependência do Alembic para templates | MIT |
| MarkupSafe | 3.0.4 | Execução / Mako, extensão nativa | BSD-3-Clause |
| h11 | 0.16.0 | Execução / Uvicorn; também testes | MIT |
| idna | 3.20 | Execução / AnyIO; também testes | BSD-3-Clause |
| opentelemetry-api | 1.45.1 | Execução / dependência obrigatória do FastAPI | Apache-2.0 |
| pydantic | 2.14.2 | Execução / FastAPI | MIT |
| pydantic-core | 2.50.2 | Execução / Pydantic; extensão nativa | MIT |
| starlette | 1.10.0 | Execução / FastAPI | BSD-3-Clause |
| typing-extensions | 4.16.0 | Execução / tipagem e dependências | PSF-2.0 |
| typing-inspection | 0.4.4 | Execução / FastAPI e Pydantic | MIT |
| colorama | 0.4.6 | Desenvolvimento / pytest no Windows | BSD-3-Clause |
| httpcore2 | 2.16.0 | Desenvolvimento / HTTPX2 | BSD-3-Clause |
| iniconfig | 2.3.1 | Desenvolvimento / pytest | MIT |
| packaging | 26.3 | Desenvolvimento / pytest | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.7.0 | Desenvolvimento / pytest | MIT |
| pygments | 2.21.0 | Desenvolvimento / pytest | BSD-2-Clause |
| truststore | 0.10.5 | Desenvolvimento / HTTPX2 | MIT |

Evidências locais: campos License-Expression/License e classifiers dos metadados
`*.dist-info/METADATA`, presença dos arquivos de licença dos wheels instalados
e árvore `uv tree --locked --no-dev`. A licença de Colorama foi identificada como
BSD de três cláusulas pelo texto do arquivo incluído no wheel.

O lock universal inclui também `httpx2-jsfetch 1.0`, condicional a Emscripten;
esse pacote não foi instalado no Windows nem integra a cadeia validada acima.
Outras plataformas não foram validadas.

Nenhum extra standard foi solicitado. Não foram instalados SDK/exportadores
OpenTelemetry nem configurados coleta, destino de telemetria ou serviço externo.
A API OpenTelemetry aparece por dependência transitiva do FastAPI e não autoriza
introduzir infraestrutura de observabilidade.

## Triagem para o modelo proprietário e limites

As licenças declaradas na cadeia Windows acima são MIT, BSD, Apache ou PSF;
não foi identificado copyleft nessa lista de metadados. A triagem é compatível
com prosseguir no desenvolvimento proprietário, preservando as obrigações
específicas de terceiros. Não constitui aprovação dos futuros binários/instalador
nem validação de conectores, termos de plataformas ou licenças ainda não examinadas.

Ao preparar distribuição, preservar textos de licença, avisos de copyright e
disclaimers exigidos; para Apache, conferir também eventual NOTICE e alterações.
As [licenças MIT](https://opensource.org/license/mit),
[BSD-3-Clause](https://opensource.org/license/bsd-3-clause),
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) e
[licença do Python/PSF](https://docs.python.org/3/license.html)
são as referências para essas obrigações.

A análise dos artefatos finais deverá abranger Python e suas bibliotecas nativas,
as dependências incorporadas aos binários Rust (pydantic-core e ferramentas
eventualmente incluídas) e quaisquer runtimes/empacotadores escolhidos.
Metadados do wheel não substituem essa verificação. Ferramentas de teste/build
não devem ser incluídas automaticamente no instalador. Ainda não há artefato de
distribuição nem licença de terceiros removida ou relicenciada como código Desktop Messaging Lab.

## Reprodução da validação

Dentro de `backend/`:

```powershell
uv sync --locked --no-python-downloads
uv lock --check
uv pip check
uv tree --locked --no-dev
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Resultado histórico da Entrega 2: **21 testes passaram**, Ruff sem erros e cinco arquivos
Python com formatação correta; lock consistente e sync subsequente sem mudanças
de pacotes. O cache uv em outra unidade gerou aviso de cópia em vez de hardlink;
isso não impediu instalação ou testes. Não foi instalado outro Python ou ferramenta
global. Testes e estilo devem ser repetidos quando código ou dependências mudarem.

## Validação da Entrega 3 — 29/10/2026

SQLAlchemy 2.0.55 e Alembic 1.20.1 foram adicionados sem extras. A resolução uv
preservou as versões anteriores e acrescentou greenlet 3.5.6, Mako 1.4.3 e
MarkupSafe 3.0.4. Greenlet é dependência da instalação Windows do SQLAlchemy,
mesmo com uso exclusivamente síncrono; não foi adicionado um driver assíncrono.

Fontes primárias: [SQLAlchemy](https://pypi.org/project/SQLAlchemy/2.0.55/),
[Alembic](https://pypi.org/project/alembic/1.20.1/),
[greenlet](https://pypi.org/project/greenlet/3.5.6/),
[Mako](https://pypi.org/project/Mako/1.4.3/) e
[MarkupSafe](https://pypi.org/project/MarkupSafe/3.0.4/).
As licenças da tabela foram conferidas nos METADATA e arquivos incluídos nos
pacotes instalados. Greenlet inclui LICENSE e LICENSE.PSF; preservar ambos.
MIT exige preservação de copyright e licença; BSD inclui avisos e disclaimer;
PSF também tem obrigações próprias. A triagem não aprova os artefatos do futuro
instalador nem dispensa conferir componentes nativos efetivamente distribuídos.

Wheels instalados de SQLAlchemy, greenlet e MarkupSafe:
`cp314-cp314-win_amd64`; Alembic e Mako: `py3-none-any`. Importações e operações
reais SQLite/Alembic foram executadas no CPython 3.14.10. Não foi necessária
compilação de dependências nem instalação de runtime adicional.

O SQLite 3.50.4 continua sendo o módulo do Python. Mantido `journal_mode=DELETE`
devido ao [WAL-reset bug](https://www.sqlite.org/wal.html#walreset), sem troca
de driver/DLL. Reavaliar WAL com runtime corrigido e validado.

`uv tree --locked --no-dev` confirmou a cadeia; `uv pip check` verificou 30
distribuições instaladas compatíveis. O lock universal contém 31 pacotes,
incluindo o condicional Emscripten já documentado. `uv_build` continua fixado
no build-system e não integra essa contagem de execução.

A suíte final e os comandos/resultados estão registrados em README e no plano.
Os 21 casos da Entrega 2 continuam incluídos, com fixtures temporárias e sem
alterar sua finalidade. Novos testes verificam migrações reais de teste,
snapshots, falhas e subprocessos/ACLs Windows. Avisos seguem tratados como erros.

## Entrega 7 - ferramentas e artefatos (30/10/2026)

PyInstaller 6.22.3 adicionado somente ao grupo dev, com hooks-contrib 2026.9,
altgraph 0.17.5, pefile 2024.8.26, pywin32-ctypes 0.2.3 e setuptools 84.2.1
resolvidos no uv.lock. Nenhuma dependencia de execucao anterior foi atualizada.
PyInstaller: GPL-2.0-or-later com excecao do bootloader; hooks: GPL, somente build;
altgraph/pefile: MIT; pywin32-ctypes: BSD-3-Clause; setuptools: MIT. Textos locais em dist-info
foram inspecionados; bootloader COPYING preservado no candidato.

Hooks locais restringem SQLAlchemy a SQLite e impedem aliases dos backports de
importlib para setuptools vendorizado. Python 3.14 usa os modulos da biblioteca
padrao; a exclusao direta causava ValueError no grafo do empacotador.
As ferramentas e seus pacotes nao devem entrar no PYZ/distribuicao.

O candidato incorpora Python, modulos stdlib, DLLs e extensoes Windows x64 dos wheels.
OpenSSL 3.5.9 identificado nos metadados PE, SQLite 3.50.4 e zlib-ng identificados
no runtime. Referencia das dependencias nativas do CPython:
https://github.com/python/cpython/blob/v3.14.10/PCbuild/get_externals.bat.
Textos adicionais de Argon2, libffi/CFFI, OpenSSL, mpdecimal, zlib-ng, xz e termos
Microsoft em packaging/licenses, com URLs e SHA-256 no sources.json.
A composicao compilada Rust e do engine requer revisao complementar antes do
release comercial. Metadados/licencas principais nao encerram essa auditoria.

PyInstaller gerou executavel e os ensaios frozen reais passaram no desenvolvimento.
Houve recusas iniciais Code Integrity 4551/3077; depois os binarios executaram sem
alteracao de politica feita pelo agente. Nao inferir liberacao em outros Windows.
Sem troca de SQLite/WAL, hash ou migracao.
A biblioteca padrao ZIP e ordenada deterministicamente, sem modificar bytecode;
dois builds independentes do backend produziram 97 arquivos com SHA-256 identicos.

## Entrega 8 — instalador e procedência (30/10/2026)

Nenhuma dependência ou lock Python foi alterado. Os validadores usam biblioteca padrão, DPAPI do Windows e pefile/PyInstaller já disponíveis no build. Inno Setup 7.1.1 é ferramenta de desenvolvimento fornecida pelo proprietário; o cliente não instala ferramentas. Hashes e licença em installer/.

O backend instalado exige participação no Job coordenado antes de iniciar o servidor. Não houve alteração de esquema, autenticação ou migrações. Inventários e Alembic permanecem. A revisão jurídica de Rust, engine, Microsoft e demais terceiros continua pendente.

## Entrega 9 — importação e normalização

Avaliação técnica: phonenumbers 9.0.42 (Apache-2.0, sem dependências), openpyxl
3.1.5 (MIT), et-xmlfile 2.0.0 (MIT e PSF para código incorporado), defusedxml
0.7.1 (PSFL, sem dependências). Wheels e textos efetivos conferidos na .venv;
versões/hashes travados em uv.lock. Preservar LICENSE/LICENCE/avisos integrais
na distribuição. O inventário foi ampliado para a grafia LICENCE e inclui
LICENCE.python de et-xmlfile, além do MIT. Nenhuma alteração dessas bibliotecas.
Esses termos permissivos não exigem publicar o código proprietário do Desktop Messaging Lab;
a revisão jurídica do artefato comercial completo continua pendente.

openpyxl usa somente leitura e defusedxml, sem automação COM/Excel. Não depende
de pandas, NumPy, lxml ou Office. phonenumbers.data é coletado no PYZ por hook
específico para carregamento dinâmico de regiões; geocoder/carrier/timezone não
são usados. Não há runtime extra no cliente.

Fontes primárias: https://github.com/daviddrysdale/python-phonenumbers,
https://openpyxl.readthedocs.io/en/stable/ e https://github.com/tiran/defusedxml.
Metadados consultados no PyPI e textos dos wheels instalados prevalecem no inventário.
