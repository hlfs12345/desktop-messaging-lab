# Desktop Messaging Lab

Aplicação Windows experimental para estudar cadastro de contatos, consentimento, campanhas e integração local com canais de mensagens. A aplicação é apresentada como **Messaging Lab**.

O projeto preserva decisões de segurança, histórico e recuperação conservadora. Ele serve como demonstração técnica e educacional. Não é um produto pronto para uso comercial.

## Tecnologias

- Flutter Desktop para a interface Windows.
- Python 3.14 e FastAPI no backend local.
- SQLite, SQLAlchemy e migrações Alembic aditivas.
- DPAPI para proteção local de credenciais e sessões por conta Windows.
- PyInstaller, C++ nativo e Inno Setup no fluxo técnico de distribuição.
- Node.js e Baileys na integração experimental com WhatsApp.

## Funcionalidades implementadas

- Autenticação local e execução supervisionada da interface com o backend.
- Cadastro, múltiplas identidades, listas e importação CSV/XLSX.
- Consentimentos, bloqueios, opt-out, deduplicação e histórico persistente.
- Templates versionados e contas de envio com segredos protegidos por DPAPI.
- Campanhas em rascunho, revisão, preparação cifrada e executor simulado sem I/O externo.
- Integração Telegram com recepção, consentimento explícito, envio unitário e lote sequencial.
- Integração experimental WhatsApp/Baileys com pareamento explícito, sessão por conta e envio unitário confirmado.

## Estado experimental

- A simplificação recente das permissões WhatsApp ainda precisa de validação funcional pela interface.
- Lote WhatsApp, backup portátil e restauração ainda não foram implementados.
- Bundle e instalador não receberam validação final em Windows limpo.
- Assinatura de código e revisão jurídica das dependências permanecem pendentes.
- Nenhuma validação existente deve ser interpretada como aprovação para operação comercial ou envio em produção.

## Estrutura

- `frontend/`: interface Flutter e runner Windows.
- `backend/`: API local, regras de negócio, persistência e migrações.
- `poc/whatsapp-baileys/`: sidecar experimental WhatsApp.
- `installer/`: instalador e launcher nativos.
- `scripts/`: automação técnica de desenvolvimento e empacotamento.
- `docs/`: arquitetura, contexto e guias operacionais.

## Execução local

Pré-requisitos: Windows, Python 3.14, uv, Flutter, Visual Studio com C++ Desktop e dependências já instaladas.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\iniciar-dev.ps1
```

O supervisor inicia o backend e entrega a autenticação privada. Não abra o backend separadamente. Dados de desenvolvimento continuam no caminho legado `%LOCALAPPDATA%\eDisk\development` para preservar compatibilidade.

## Limitações e responsabilidade

Telegram, WhatsApp, Baileys e demais marcas pertencem aos respectivos titulares. Integrações não oficiais deste repositório não são afiliadas, patrocinadas nem endossadas pelas plataformas.

Use somente contas e contatos autorizados. Respeite consentimento, opt-out, termos das plataformas e legislação aplicável. Não publique bancos, sessões, credenciais DPAPI, contatos, exports ou logs.

Este código é experimental, proprietário e fornecido para demonstração de portfólio. Não há garantia de adequação, disponibilidade ou segurança para uso comercial.

## Documentação

- [Plano técnico](docs/PLANO_DESENVOLVIMENTO.md)
- [Contexto atual e limitações](docs/CONTEXTO_ATUAL.md)
- [Importação de contatos](docs/CONTATOS_IMPORTACAO.md)
- [Uso da simulação](docs/USO_SIMULACAO.md)
- [Instalação Windows](docs/INSTALACAO_WINDOWS.md)
