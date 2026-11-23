# Execuções de simulação

O mock simula mensagens de WhatsApp sem enviar nada para fora do computador.
“Aceito pelo mock” não significa mensagem entregue ou lida. Esta funcionalidade
não habilita contas ou provedores reais.

## Preparar e iniciar

1. Abra **Campanhas**, selecione uma campanha com conta mock e acesse
   **Preparação e histórico**.
2. Revise e prepare a fotografia com o cenário escolhido. Confirme a preparação
   e autorize a simulação no painel de autorização.
3. Com essa preparação confirmada selecionada, abra **Execuções mock** pelo
   botão da barra superior. Escolha **Revisar início da preparação confirmada**.
4. Confira o cenário, os incluídos, os elegíveis e os ignorados atuais.
5. Escolha **Confirmar início**, leia o aviso e marque a confirmação. O botão
   final fica desabilitado até essa marcação.

Confirmar a fotografia ou autorizar a simulação são decisões separadas do início.
Entrar, abrir telas e atualizar consultas não iniciam processamento.

## Acompanhar e consultar histórico

O botão **Execuções mock** também está na lista de campanhas. Esse acesso
permite consultar o histórico mesmo sem abrir a fotografia. Selecione uma
execução para ver estado, datas e contadores. Abra um item para consultar suas
tentativas. Use **Anterior** e **Próxima** para navegar; cada página contém até
20 registros, mesmo em campanhas de 8.000 destinatários.

O painel separa execução registrada de processamento ativo. Exibe pendentes,
reservados, em processamento, aceitos pelo mock, falhas, desconhecidos, ignorados
e interrompidos. A conta identificada no cabeçalho é a conta atual da campanha.
As datas são apresentadas no horário local do computador.

Enquanto uma execução em andamento estiver aberta, o painel consulta o progresso
a cada cinco segundos após terminar a consulta anterior. Não há sobreposição de
consultas nem ativação de processamento por atualização. Sair da tela, perder a
sessão ou fechar o aplicativo interrompe essas consultas.

## Pausar e retomar

**Pausar** solicita confirmação. Uma tentativa já admitida pode terminar;
“Pausa solicitada” não promete cancelamento instantâneo. Aguarde o estado
registrado como **Pausada**.

Para continuar, escolha **Revisar retomada**, confira as condições atuais e
confirme novamente. O aplicativo revalida autorização, conta e elegibilidade.
Dados alterados podem exigir outra revisão. Não há retomada automática após
login, reconexão ou reinício.

Um resultado **desconhecido** é uma incerteza, não uma falha definitiva nem prova
de que nada ocorreu. Esses itens permanecem no histórico e não são repetidos na
retomada. A interface não oferece reenvio automático desses resultados.

## Falha de comunicação ou ativação

Se aparecer **confirmação pendente**, a operação pode ter sido gravada mesmo que
a resposta não tenha chegado. A tela conserva o pedido original enquanto estiver
aberta e consulta o estado persistido. Use **Consultar estado** ou **Repetir a
mesma operação**. A repetição mantém os mesmos dados e identificador; não cria
uma operação diferente e não ativa processamento por efeito de replay.

Um recibo histórico confirma o comando original, não o estado atual de progresso.
Uma execução gravada sem ativação será identificada como tal. Consulte o estado
e faça uma retomada explícita quando permitida. Nunca considere timeout,
cancelamento da consulta ou fechamento da tela como prova de desfazimento.

Pedidos pendentes ficam apenas na memória da tela; não existe uma fila de
comandos no Flutter. Ao sair, perder a sessão ou reabrir o aplicativo, consulte o
histórico. Nenhum pedido antigo é repetido automaticamente.

## Queda e fotografia indisponível

Após queda, o backend reconcilia o histórico antes de permitir processamento.
Uma reserva sem invocação pode ser interrompida e voltar a pendente. Uma possível
invocação sem resultado confirmado fica desconhecida. Resultados já concluídos
são preservados. Execuções recuperáveis ficam pausadas, exigindo revisão nova.

Se a chave/fotografia estiver indisponível, o histórico operacional continua
consultável quando autenticação e banco estiverem seguros. Início e retomada
ficam sujeitos à autenticação integral da fotografia; solicite ao administrador
o procedimento de recuperação existente. Recuperação de chave não é restauração
completa de backup nem garantia de portabilidade para outro perfil Windows.
### Descadastramento com confirmação pendente

O SQLite é a autoridade de durabilidade. O sucesso do pedido confirma evento e
bloqueio persistidos. Uma falha de persistência fecha a admissão de novas
tentativas na abertura atual; uma tentativa já admitida pode terminar.

`optout_rejeitado` significa ausência comprovada de registro durável: atualize
o estado e envie novamente conscientemente. Esse pedido não é recuperável após
reinicialização. `optout_indeterminado` significa que houve persistência parcial
ou não foi possível conferir o resultado; consulte o histórico e repita o mesmo
UUID/corpo quando disponíveis, sem presumir rollback ou criar duplicidade.

Pendências confirmadas no SQLite sobrevivem à reabertura e impedem admissões
afetadas até a confirmação durável do bloqueio. Arquivos JSON e manifestos
legados são preservados, mas não criam intenções nem comandam recuperação.
Reiniciar, fazer login ou consultar histórico não inicia processamento automático.
