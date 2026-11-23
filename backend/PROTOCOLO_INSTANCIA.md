# Canal privado da instância — Entregas 5 e 18 (Marco C)

O supervisor nativo é o proprietário dos processos. O Python não pesquisa
processos ou portas e não abre HTTP sem receber INIT válido por stdin privado.
Este contrato não é uma API de plugins ou um protocolo de conectores.

## Formato v1

Cabeçalho de 8 bytes, little endian: `magic[4]="ED5P"`, `version:u8=1`,
`kind:u8`, `payload_length:u16`. Máximo de 512 bytes de payload; nesta versão
os tamanhos abaixo são exatos. Rejeitar cabeçalhos antes de ler/alocar payload.
Todos os payloads começam com `execution_id[16]` aleatório, em bytes brutos.

| Tipo | Código | Tamanho do payload | Campos após execution_id |
|---|---|---|---|
| INIT | 1 | 48 | secret[32] bruto |
| HELLO | 2 | 20 | PID Python:u32 |
| PREPARING | 3 | 16 | nenhum |
| READY | 4 | 18 | porta:u16, diferente de zero |
| ERROR | 5 | 18 | código:u16 |
| STOP | 6 | 16 | nenhum |
| STOPPED | 7 | 16 | nenhum |
| LIFETIME | 8 | 32 | capability[16] bruto |
| QUIESCE | 9 | 16 | nenhum |

Códigos ERROR: 1 protocolo, 2 configuração, 3 banco em uso, 4 persistência,
5 inicialização. Nunca incluir exceções, caminhos, frames ou segredos.

Sequência: supervisor envia INIT uma vez; backend envia HELLO, PREPARING,
READY. ERROR encerra a inicialização/execução. STOP pode chegar antes de READY;
o backend termina a limpeza e envia STOPPED. Duplicatas e mensagens fora de
ordem são falhas. EOF sem STOP é perda do supervisor, não autorização para
permanecer ativo. EOF no meio de um frame sempre é erro.

Extensão C do v1: runner/backend devem ser atualizados juntos. O supervisor envia
LIFETIME uma vez após INIT, antes de QUIESCE/STOP. A capacidade aleatória, distinta
do segredo HTTP, identifica o evento manual-reset `Local\\eDisk-close-<execution>-<capability>`.
Não registrar nome/capacidade ou passá-los por argumentos, ambiente ou arquivos.
O backend exige participação em Job e abre o evento somente com SYNCHRONIZE.
O nome evita depender do redirector Python repassar um handle adicional.

O primeiro WM_CLOSE sinaliza esse evento antes de qualquer deferimento por diálogo
ou confirmação Dart. O supervisor envia QUIESCE sem esperar pelo ACK. QUIESCE
fecha admissão/certificados e permite drenagem, mas não encerra HTTP sozinho.
STOP mantém o ACK/fallback de 250 ms e inicia a parada completa. Evento sinalizado,
STOP, EOF ou protocolo inválido impedem novas admissões. A última barreira ocorre
fora de transação SQLite imediatamente antes do mock; chamada já admitida pode
concluir. Ausência de LIFETIME conserva HTTP/histórico para clientes de teste,
mas não autoriza ativação operacional. Não há execução por login/startup/consulta.

STOPPED exige lifespan encerrado, worker drenado, sessões revogadas e persistência
fechada; erro de drenagem, force_exit ou falha do lifespan produz ERROR/saída não
zero. STOP antes de READY continua permitido, inclusive no retorno antecipado do
startup do Uvicorn. O Job continua sendo a contenção de encerramentos abruptos.

## Identidade e HTTP

O runner gera execution_id e segredo com BCryptGenRandom. O PID de HELLO é
aberto e conferido contra o Job; pode diferir do PID do redirector da .venv.
PID sozinho, porta ou resposta HTTP não comprovam a identidade.

O segredo HTTP é a representação hexadecimal minúscula dos 32 bytes, exatamente
64 caracteres: header `Authorization: Bearer <token>`. Não passa por argumentos,
ambiente, arquivo, URL ou logs. Não salvar transcrições do canal. STDOUT pertence
exclusivamente ao protocolo, stderr é drenado separadamente pelo supervisor.

Todas as rotas exigem autorização; ausente/inválida retorna 401. Requisições com
Origin (inclusive null) ou Sec-Fetch-* retornam 403, mesmo com segredo válido.
Sem cookies, CORS permissivo, proxy headers ou documentação HTTP habilitada.
As respostas recebem Cache-Control: no-store. Health autorizado mantém 200/503.

## Garantias e limites

O socket usa IPv4, 127.0.0.1:0 e SO_EXCLUSIVEADDRUSE no Windows; permanece aberto
até ser usado pelo Uvicorn. READY só é emitido após startup HTTP e lifespan.
Disponível na interface exige também uma consulta health autenticada bem-sucedida.

O Job é associado na criação via JOB_LIST e não permite breakaway. Apenas o
supervisor possui seu handle, não herdável. Fechamento normal pede STOP; fechamento
forçado encerra o Job sem prometer execução do lifespan. O lock e a recuperação
SQLite existentes continuam responsáveis pela próxima abertura segura.

Essas medidas protegem contra acesso casual e páginas web, não contra malware
com privilégios para ler memória, duplicar handles ou modificar o aplicativo.
O segredo fica em memória; buffers nativos são zerados por melhor esforço.
Python/Dart e bibliotecas HTTP podem manter cópias que não permitem apagamento
garantido. Autorização de instância não substitui login de usuário.
