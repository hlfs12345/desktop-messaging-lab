# Contexto atual do Desktop Messaging Lab

Atualizado em 22/11/2026 no checkout `bfe0c69`, com alterações locais ainda sem commit.

O desenvolvimento comercial foi interrompido. O projeto agora é uma demonstração técnica experimental de portfólio; a aplicação usa o nome Messaging Lab.

## Estado atual

- Aplicativo Windows em monólito modular local: Flutter, FastAPI, SQLite/SQLAlchemy e Alembic.
- Autenticação local, credenciais e sessões por conta usam proteção DPAPI.
- Campanhas possuem rascunho, fotografia cifrada, execução `mock` e histórico persistente.
- Telegram possui recepção, consentimento explícito, envio unitário e lote sequencial com pausa e retomada explícitas.
- WhatsApp/Baileys possui pareamento por ação, isolamento de sessão por conta e envio unitário confirmado.
- Revisão Alembic mais recente: `0019_whatsapp_envio_unitario`.

## Decisões vigentes

- Flutter usa somente a API; apenas o backend acessa o SQLite de negócio.
- Dados persistentes ficam fora do repositório e da instalação.
- Consentimento não substitui aptidão de envio. Bloqueios e opt-outs sempre prevalecem.
- Intenção de envio é persistida antes da rede. Resultado ambíguo fica desconhecido e não permite reenvio automático.
- WhatsApp QR permanece experimental e sem aprovação comercial.
- Desconexão temporária, desativação e desvínculo confirmado de sessão são operações distintas.

## Alterações locais em validação

- Autorizações WhatsApp externas podem ser registradas para uma lista; identidades ausentes são criadas na mesma transação.
- Bloqueios globais, por canal, revogações e descadastramentos pendentes permanecem preservados.
- Cadastro mostra ações diretas para bloquear, remover bloqueio administrativo e registrar nova autorização.
- Fluxo unitário usa uma confirmação operacional.
- Estas alterações não foram validadas por testes, análise, build, migração ou envio real, conforme restrição da entrega.

## Problemas conhecidos

- Lote WhatsApp ainda exige migração e extensão da fila existente.
- Backup portátil criptografado e restauração continuam pendentes.
- Bundle e instalador ainda não possuem validação final em Windows limpo.
- Assinatura e revisão jurídica de dependências permanecem pendentes. Não existe aprovação para uso comercial.

## Próximo passo

- Publicar somente uma cópia saneada, preferencialmente em novo repositório com histórico limpo.
- Validar pela interface cadastro, autorização individual e por lista, preservação de bloqueios e confirmação unitária sem envio real.
- Planejar lote WhatsApp separadamente, reutilizando o executor atual.
- Não há autorização para envio real, testes, build, instalador, migração, commit ou push.

Referências: `AGENTS.md` e `docs/PLANO_DESENVOLVIMENTO.md`.
