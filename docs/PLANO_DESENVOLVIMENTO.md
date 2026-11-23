# Plano técnico do Desktop Messaging Lab

Atualizado em 22/11/2026.

## Produto e stack

Projeto desktop experimental para Windows, preservado como demonstração técnica de portfólio. O desenvolvimento comercial foi interrompido.

- Interface: Flutter Desktop/Windows.
- Backend local: Python 3.14 e FastAPI.
- Persistência: SQLite, SQLAlchemy e migrações Alembic aditivas.
- Segurança local: autenticação própria, Argon2id, DPAPI por conta e transporte privado em loopback.
- Execução: monólito modular supervisionado; Flutter usa somente a API.
- Distribuição: bundle PyInstaller, launcher nativo e instalador Inno Setup.

Não adicionar broker, Docker, Redis, Celery, serviço permanente ou nuvem sem necessidade aprovada.

## Arquitetura simples

- Rotas HTTP tratam transporte e delegam regras aos módulos.
- Serviços aplicam consentimento, idempotência, transações e regras de negócio.
- Persistência fica no backend; nenhuma tela abre o SQLite.
- Provedores compartilham contrato mínimo. `mock` nunca faz I/O externo.
- Dados persistentes ficam fora do repositório e da pasta de instalação.
- Credenciais e sessões sensíveis usam DPAPI e isolamento por conta Windows.
- Resultado externo incerto é preservado como desconhecido e nunca reenviado automaticamente.
- Consentimentos, opt-outs, bloqueios e deduplicação são revalidados antes de cada tentativa.

## Módulos concluídos

- Fundação Windows, supervisor, autenticação local e persistência segura.
- Contatos, múltiplas identidades, listas e importação CSV/XLSX.
- Consentimentos, bloqueios, descadastramento e histórico imutável.
- Templates versionados e contas de envio com segredos DPAPI.
- Campanhas em rascunho, revisão, fotografia cifrada e confirmação explícita.
- Provedor e executor `mock`, fila persistente, pausa, retomada e histórico.
- Telegram: recepção, opt-in explícito, opt-out, envio unitário e lote sequencial.
- WhatsApp/Baileys: pareamento por ação, sessão protegida por conta e envio unitário confirmado.
- Migração mais recente: `0019_whatsapp_envio_unitario`.

## Pendências

- Validar pela interface a simplificação local de permissões WhatsApp.
- Estender a fila existente para lote WhatsApp, sem criar fila paralela.
- Implementar backup portátil criptografado e restauração validada.
- Validar bundle e instalador em Windows limpo.
- Resolver assinatura e revisão de licenças antes de qualquer distribuição.
- WhatsApp QR continua experimental e sem aprovação comercial.

## Próximas entregas

1. Validação funcional da autorização WhatsApp individual e por lista, bloqueios e confirmação unitária sem envio real.
2. Planejamento e implementação incremental do lote WhatsApp sobre o executor existente.
3. Backup e restauração seguros.
4. Validação técnica do pacote Windows após solicitação explícita.

Cada entrega deve preservar dados e histórico. Migrações são criadas somente quando necessárias e executadas pelo ciclo aprovado da aplicação, nunca durante desenvolvimento por iniciativa do agente.
