# POC WhatsApp Baileys

Processo Node isolado. Não envia mensagens e não conecta automaticamente.

## Integração

Na Entrega 25B, o backend inicia este processo por conta através de pipes privados
autenticados. Abrir o detalhe da conta apenas consulta o estado; não conecta nem
gera QR automaticamente. Use a interface Flutter para parear, reconectar,
desconectar temporariamente ou desvincular.

Requisitos de desenvolvimento: Windows, Node.js 26 LTS, dependências npm já
instaladas e `backend/.venv` existente. O instalador não é alterado nesta entrega.

A sessão integrada fica cifrada por DPAPI do usuário e isolada por
`conta_envio_id` no caminho legado `%LOCALAPPDATA%\eDisk\whatsapp-baileys\`. A sessão isolada
da POC 25A permanece preservada no caminho legado e não é atribuída
automaticamente a uma conta. QR, chaves e credenciais não são gravados em logs
ou no repositório.

## Empacotamento futuro

Fixados no `package-lock.json`: Baileys 6.7.24 e `qrcode-terminal` 0.12.0.
O instalador deverá incluir runtime privado Node 26 LTS, sidecar, dependências e
licenças npm. O bridge usa o runtime Python/backend existente para DPAPI. Nenhum
serviço Node, Docker ou instalação manual deverá ser exigido do cliente.

