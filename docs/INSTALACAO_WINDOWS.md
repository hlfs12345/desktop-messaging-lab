# Instalação Windows

O estado vigente e as pendências estão em [CONTEXTO_ATUAL.md](CONTEXTO_ATUAL.md).
Os resultados históricos não aprovam um payload atualizado. A receita técnica executa suítes completas, Debug/Release,
backend congelado e ensaios com dados descartáveis antes da promoção do bundle.
O campo migration_head é obtido do catálogo do backend, atualmente
0019_whatsapp_envio_unitario. Nenhuma instalação executa migração: ela continua no
startup do backend, sob exclusividade e snapshot.

Para ensaiar instalador atualizado sem sobrepor instalações existentes, use
`-InstanciaEnsaio` com UUID hexadecimal novo no canal técnico. Verifique que as
pastas de programa e dados dessa instância ainda não existem. Ensaios de fixture
nativa e ensaios do payload completo são evidências distintas; ambos continuam
separados de Windows limpo, assinatura, auditoria independente e aprovação comercial.

Validação técnica local concluída, com ressalvas; revisão do proprietário pendente. Distribuição comercial ainda não aprovada. Não confundir os instaladores históricos 0.1.0.1 com os novos artefatos corretivos.

## Programa e dados

- Candidato: `%LOCALAPPDATA%/Programs/eDisk`.
- Técnico: `%LOCALAPPDATA%/Programs/eDisk-Tecnico`.
- Produção: `%LOCALAPPDATA%/eDisk`.
- Desenvolvimento: `%LOCALAPPDATA%/eDisk/development`.
- Instalação técnica: `%LOCALAPPDATA%/eDisk-validacao-instalador/<nome-da-instalação>`.
- Bundle técnico independente: `%LOCALAPPDATA%/eDisk-validacao-tecnica`.

O data/ do programa contém recursos Flutter. SQLite, autenticação, backups e logs ficam externos. O login não criptografa SQLite; instalação por usuário não protege os binários contra malware desse mesmo usuário.

## Ferramentas e versão

A fonte autoritativa é installer/produto.json, confrontada com pubspec.yaml e pyproject.toml. O build confere o recurso PE estruturado de variante e versão. Não alterar apenas build-info para mudar o perfil.

Preparar dependências conforme README. Os scripts não instalam ferramentas. Inno Setup 7.1.1 x64 deve corresponder aos hashes de installer/toolchain.lock.json, incluindo engine, arquivos auxiliares e licença. Atualização do lock exige revisão deliberada.

## Gerar bundle técnico

Na raiz:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\empacotar-windows.ps1 -Canal tecnico
```

O script executa regressões, builds, auditorias e ensaios do bundle antes de fechar os inventários e a origem DPAPI. A saída é nova em dist/. Falhas conservam staging e relatórios. O cliente final não precisa de Python, Flutter, uv, Visual Studio ou Inno Setup.

## Gerar instalador técnico

Usar o bundle aprovado e o SHA-256 exato do seu manifesto:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gerar-instalador-windows.ps1 `
  -Bundle 'CAMINHO-ABSOLUTO-DO-BUNDLE' `
  -ManifestSha256 'SHA256-DO-MANIFESTO' `
  -Canal tecnico `
  -Iscc 'C:\Program Files\Inno Setup 7\ISCC.exe' `
  -Destino 'CAMINHO-ABSOLUTO-NOVO-DENTRO-DE-dist'
```

-InstanciaEnsaio UUID_HEXADECIMAL_32 produz AppId e pasta exclusivos, somente no canal técnico. Os pontos de falha do Inno pertencem apenas a esse instalador de ensaio. O launcher de teste da coordenação não integra o bundle nem o setup normal.

## Candidatos e aprovação independente

As duas etapas candidatas exigem Git limpo. O build produz um ID de origem e o hash do registro DPAPI fora do bundle. O proprietário deve revisar esse registro:

```powershell
.\scripts\aprovar-origem-bundle.ps1 -RegistroId 'ID' -RegistroSha256 'HASH-DO-REGISTRO'
```

Somente depois da revisão, repetir com -Confirmar. O comando retorna o hash da aprovação independente. Gerar o setup com -Canal candidato, -AprovacaoId ID e -AprovacaoSha256 HASH_DA_APROVACAO. Não usar o hash do registro de origem como hash da aprovação.

A aprovação fica no cofre fixo do usuário, LocalAppData/eDisk-build-trust/v1, protegida por DPAPI. Não copiar um JSON de dentro do bundle como aprovação. Ausência, adulteração ou divergência bloqueia a geração. O modelo confia no proprietário e nas ferramentas locais; não equivale a assinatura ou atestado de terceiro. Candidato compilado ainda exige ensaio da GUI em perfil Windows isolado e aprovação comercial.

## Instalar e abrir

Execute o instalador do canal correto. Ele não solicita administrador, não abre o aplicativo e não executa migrações. O atalho aponta para edisk-launcher.exe. Use esse atalho; o runner e o backend instalados recusam abertura fora do coordenador.

Antes de abrir o runner, o launcher confere active.txt, canal, versão, recurso PE, metadados aprovados e todos os hashes. Instalação pendente ou contraditória exige reparação. Na primeira abertura, crie o administrador. Reaberturas usam o login existente.

## Códigos de saída do launcher

A partir do artefato técnico 0.1.0.6, o launcher usa este contrato:

| Código | Significado |
| --- | --- |
| 0 | Runner criado e retomado, retorno do runner igual a zero e encerramento confirmado. |
| 1 | Instalação inválida, incompleta ou em manutenção; runner não iniciado. |
| 2 | Falha ao preparar ou registrar a coordenação; runner não iniciado. |
| 3 | Não foi possível confirmar o encerramento ou consultar o resultado do runner. |
| 4 | Falha de CreateProcessW ou ResumeThread ao iniciar o runner. |
| 5 | Runner iniciado, encerramento confirmado e retorno do runner diferente de zero. |

O código original de GetLastError é capturado imediatamente após a falha de CreateProcessW ou ResumeThread e exibido como “Código do Windows”. No caso 5, o retorno original do runner é exibido como “Código do aplicativo”; ele não é propagado diretamente como saída do launcher, evitando colisão com os códigos acima. As mensagens não incluem caminhos, argumentos, credenciais ou conteúdo do backend.

A saída zero depende tanto da execução quanto da confirmação de término. Confirmar um Job vazio após uma falha de criação não equivale a sucesso. Em caso de falha de inicialização, o código 4 tem precedência sobre falhas posteriores de confirmação; setup e desinstalador continuam decidindo exclusivamente pelas travas e registros do protocolo 3, nunca pelo código de saída. Os marcadores terminais válidos permanecem até a manutenção confirmar também o término do coordenador. Enquanto a mensagem estiver aberta, o launcher ainda está em execução.

Os testes de falha real permanecem no código e nos testes automatizados do launcher.

## Atualizar e reparar

Feche as instâncias e execute a mesma versão ou uma posterior do mesmo canal. Downgrade e mesma versão com payload diferente são recusados. Não há atualização automática ou download.

O protocolo 3 mantém um Job e handles dos processos até confirmar o término efetivo. O marcador terminal em runtime permanece até a manutenção comprovar o término dos descendentes e do coordenador, validando PID, criação, checksum e sinalização do processo. Identidade divergente ou acesso indisponível bloqueiam. Setup e desinstalador recusam manutenção se houver leitor ou estado não confirmado. Timeout não concede permissão.

Após morte abrupta do coordenador, uma confirmação pode ficar pendente mesmo com a janela fechada. Solicite suporte; não apague arquivos de runtime, installation.id ou maximum-version.txt para contornar o bloqueio. Não procure ou encerre python.exe por nome.

Instalações históricas dos protocolos 1 e 2 são recusadas. Sua migração exige um procedimento de suporte que comprove encerramento dos processos; não há conversão automática nesta rodada.

Falhas de cópia, ativação ou finalização mantêm pending.txt. Reexecutar o mesmo instalador ou uma versão posterior pode reparar, desde que não exista encerramento sem confirmação. Migrações continuam exclusivamente no backend, com o lock e snapshot existentes. Banco futuro é recusado; não há downgrade automático de esquema.

## Desinstalar e reinstalar

Use Aplicativos Instalados do Windows após fechar o Messaging Lab. A exclusividade e a confirmação dos processos também são exigidas na desinstalação. O log Inno acumulado remove apenas arquivos conhecidos, incluindo versões anteriores; arquivos desconhecidos são preservados. Dados externos, autenticação, backups e logs permanecem. Não existe opção de apagar dados.

installation.id e maximum-version.txt permanecem deliberadamente para reconhecer a pasta e impedir downgrade. A reinstalação preserva o login. Não selecionar diretórios desconhecidos ou dados reais para ensaios.

## Logs e suporte

O Inno possui logs técnicos. Os ensaios automatizados geram um UUID por tentativa em build/installer-attempts, com log e recibo que associa executável, hash e resultado. Falhas e reparações usam arquivos distintos. Não apagar logs pessoais ou arquivos desconhecidos.

## Distribuição comercial

Permanecem pendentes Windows limpo, usuário padrão sem ferramentas, abertura offline, proteções padrão, assinatura e revisão jurídica de terceiros, inclusive runtime e licenças Microsoft. Preservar LICENSES, THIRD_PARTY_NOTICES e Inno-LICENSE. Não desativar SmartScreen, Smart App Control, Defender ou Code Integrity para executar binários não assinados.

Nos bundles técnicos finais, --edisk-producao é recusado também fora de releases. O perfil de produção pertence exclusivamente ao runner candidato compilado para esse canal. O protocolo IPC privado permanece na versão 1; protocolo 3 é a revisão da coordenação de instalação.
