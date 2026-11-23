# Contatos e listas

Entre com seu login local. As opções Contatos, Listas e Importações ficam na
área autenticada. O Messaging Lab continua funcionando localmente e offline para cadastro.

## Cadastro

Novo contato exige telefone; nome e observações são opcionais. Confira o país
sugerido antes de salvar um número nacional. Para número internacional, use +
e o código do país. Não se acrescentam DDD ou nono dígito para corrigir entradas.
Formato válido não comprova existência, titularidade ou conta em mensageiro.

Abra o contato para editar, adicionar/arquivar telefones e organizar suas listas.
Um telefone pertence a um único contato, inclusive se arquivado. Não há fusão por
nome nem transferência de telefone. Para trocar um número, arquive o antigo e
adicione o novo. Arquivar preserva histórico; o filtro Arquivados permite consultar.
Não existe exclusão física nem reativação por importação. Retenção/anonimização
e consentimento serão tratados em entregas próprias.

Crie listas e clique nelas para consultar participantes. Em Organizar listas,
marque ou desmarque as associações e salve a página. Remover de uma lista não
exclui o contato. Há pesquisa e paginação; listas arquivadas mantêm associações.
Uma edição desatualizada exige nova consulta antes de salvar.

## Importar

1. Escolha Excel .xlsx ou CSV no diálogo Windows. Não é necessário instalar Excel.
2. Selecione a aba; para CSV, confira separador e codificação. UTF-8 é o padrão;
   Windows-1252 deve ser escolhido explicitamente. Cabeçalho é opcional.
3. Use Ver primeiras linhas e mapeie colunas por posição. Nome e observações são
   opcionais; telefone é obrigatório. Uma linha corresponde a um telefone.
4. Confira o país de referência. Arquivos mistos precisam de números com + ou
   coluna de país (código ISO de duas letras, por exemplo BR, GB, US, IT).
5. Escolha a política para existentes e as listas de destino. O padrão preserva
   todos os dados. Preencher vazios e Substituir exigem seleção de campos.
6. Valide o arquivo inteiro, consulte contagens e linhas com erros/conflitos.
7. Confirme explicitamente a gravação das linhas válidas.
8. Consulte o resultado no histórico e, se necessário, salve o relatório CSV.

Campos vazios nunca apagam valores. Listas anteriores e UUIDs são preservados.
Reimportação sem mudança não altera datas dos contatos. Nomes iguais não unem
pessoas e números diferentes não são associados automaticamente.

Repetições equivalentes são identificadas. Dados conflitantes para o mesmo
telefone, ou atualizações incompatíveis de um contato com vários telefones,
ficam inválidos: corrija na origem e valide novamente. Não existe última linha
vencedora. Arquivados são reconhecidos e ignorados, sem reativação.

Linhas vazias são contabilizadas e ignoradas. Fórmulas em colunas mapeadas são
inválidas, não executadas. Telefones devem preferencialmente estar como texto.
Células numéricas exatas permitidas recebem aviso para conferir zeros; formatos
ambíguos e perda de precisão são rejeitados. Dígitos já perdidos não são recuperados.

## Limites e interrupção

Limites iniciais: 10 MiB por arquivo, 20.000 registros, 100 colunas, células de
até 32.768 caracteres; nome até 120 e observações até 2.000 caracteres.
XLSX tem limites adicionais: até 100 abas, 1.000 entradas ZIP, total descompactado
de 100 MiB, 50 MiB por entrada e razão de expansão até 200. Não há extração para disco.
Macros, arquivo protegido/criptografado, .xls e formatos incompatíveis são recusados.
Trechos encurtados na amostra são marcados como prévia; dados gravados não são truncados.

Uma importação fica ativa por vez. Arquivo e plano ficam em memória por até
30 minutos. Voltar à tela anterior não cancela a operação: reabra-a no histórico.
Reabrir uma prévia preserva sua configuração; use a validação novamente se mudar opções.
Cancelar antes da confirmação não altera contatos. Após reinício, operações
pendentes ficam interrompidas; escolha o arquivo novamente. Não há retomada parcial.

Na confirmação, todas as alterações válidas, associações, resultados e auditoria
confirmam ou revertem juntas. Se o cadastro mudou, a prévia deve ser revalidada.
Se houver timeout, consulte o resultado antes de tentar novamente: a resposta pode
ter se perdido depois do commit. A mesma confirmação não repete efeitos.

## Privacidade e limites locais

Importar não registra consentimento e não verifica WhatsApp/Telegram. Não há envios.
Arquivo original não é salvo pelo backend; histórico conserva nome sem caminho,
hash, usuário, configuração, contagens e resultados sem cópia integral das linhas.
Após confirmar, o relatório conserva linha, resultado e motivo; não copia nomes
ou números completos. Sua exportação contém apenas esses dados minimizados.
Auditoria registra IDs e campos alterados, sem valores pessoais integrais.

Tokens continuam somente em memória. Logout, expiração e desconexão invalidam
operações e retiram as rotas de negócio da interface. SQLite e snapshots internos
continuam sem criptografia; o login não impede acesso ao arquivo pelo usuário Windows.
Backups portáveis e retenção automatizada permanecem fora desta entrega.
