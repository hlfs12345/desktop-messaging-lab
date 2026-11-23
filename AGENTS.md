# AGENTS.md — Regras de desenvolvimento do Desktop Messaging Lab

Projeto Windows proprietário. Responda em português brasileiro e use `$caveman` até o usuário pedir `stop caveman` ou `normal mode`.

## Fontes de verdade

- Regras de trabalho: este arquivo.
- Arquitetura, stack, módulos e plano: `docs/PLANO_DESENVOLVIMENTO.md`.
- Estado atual, decisões e problemas conhecidos: `docs/CONTEXTO_ATUAL.md`.
- Leia somente os trechos e arquivos necessários para a tarefa.

## Implementação

- Faça somente o escopo solicitado e a menor alteração coerente.
- Prefira código pragmático, explícito, simples e legível.
- Reutilize componentes existentes.
- Evite abstrações, interfaces, classes, dependências e arquivos sem necessidade concreta.
- Não refatore backend ou Flutter por estética.
- Preserve Flutter/Windows, FastAPI, SQLite, SQLAlchemy, Alembic e o monólito modular.
- Flutter usa a API; somente o backend acessa o SQLite de negócio.
- Não introduza serviços, tecnologias ou infraestrutura sem aprovação.

## Segurança e integridade

- Nunca exponha senhas, tokens, sessões, PII completa ou conversas.
- Preserve autenticação, autorização, loopback estrito, CORS restrito, DPAPI e isolamento por conta.
- Preserve transações, histórico, auditoria, idempotência e recuperação conservadora.
- Nunca enfraqueça consentimento, opt-out, bloqueios, deduplicação ou revalidação antes do envio.
- Resultado incerto nunca autoriza reenvio automático.
- Não altere dados persistentes nem apague, recrie ou carimbe bancos existentes.
- Mudança de esquema exige nova migração Alembic aditiva; crie-a somente quando necessária e não a execute.
- Não faça envio real, importação destrutiva ou exclusão de dados sem autorização explícita.

## Limites de execução

- Não crie nem execute testes automaticamente. O usuário valida funcionalmente pela interface.
- Não execute migrações, builds, bundles ou instaladores sem solicitação.
- Não instale dependências.
- Não faça auditorias gerais ou inspeções extensas.
- Não faça commit ou push automaticamente.
- Preserve alterações locais do usuário e não use operações Git destrutivas.

## Documentação e resposta

- Evite duplicação. Atualize somente `docs/PLANO_DESENVOLVIMENTO.md` ou `docs/CONTEXTO_ATUAL.md` quando a tarefa mudar seu conteúdo.
- Use `.agents/skills/contexto-edisk/SKILL.md` somente para marcos duradouros.
- Responda curto: **Feito**, **Verificação** e **Atenção** somente quando houver conteúdo.
