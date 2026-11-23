# Dependências do frontend - entregas 4 a 6

## Atualização da Entrega 6 — 29/10/2026

Nenhum pacote pub novo, upgrade de SDK ou plugin Windows. Autenticação usa
ChangeNotifier, ListenableBuilder, Form, TextEditingController e Timer do SDK,
com o transporte HTTP existente extraído para TransporteLocal.
Token do usuário fica privado no cliente e apenas em memória. Não se adicionou
shared_preferences, armazenamento seguro, DPAPI, gerenciador de navegação/estado
ou alteração no MethodChannel/runner. NFC e regras finais são validadas no backend.

Temas, localizações, fontes e avisos de terceiros abaixo foram preservados.
Builds debug/release são validações técnicas, sem autorização de distribuição comercial.

## Atualização da Entrega 5 — 29/10/2026

Nenhum pacote pub novo ou versão alterada. A supervisão usa C++17, SDK Windows,
Job Objects, CreateProcessW/STARTUPINFOEXW e pipes. BCryptGenRandom exige link
com bcrypt.lib; bcrypt.dll é componente do Windows, não um novo arquivo a
redistribuir nesta entrega. MethodChannel/StandardMethodCodec pertencem ao SDK
Flutter existente; não se adicionou plugin de janela, FFI ou gerador de código.

O harness nativo usa o MSVC/CMake já fornecido pelo Visual Studio, apenas em
desenvolvimento. Scripts de teste usam biblioteca padrão Python, Win32, protocolo
machine do SDK Flutter e VM Service somente em debug, sem dependência adicional.
O script de desenvolvimento não fornece runtimes ao cliente: essa validação
continua nas entregas 7/8. Obrigações de avisos e análise dos artefatos finais
abaixo permanecem válidas; release segue restrito à validação técnica.

Verificação local em **29/10/2026**, Windows x64, Flutter **3.47.8** stable,
Dart **3.13.7**. Resolução efetiva em `pubspec.lock`; grafo conferido em
`.dart_tool/package_graph.json`, caminhos em `package_config.json` e textos
`LICENSE` dos pacotes instalados. Não se instalou outro SDK global.

## Dependências diretas e justificativa

| Dependência | Versão resolvida | Finalidade |
|---|---|---|
| flutter | SDK 3.47.8 | Interface Windows e Material 3 |
| flutter_localizations | SDK 3.47.8 | Componentes oficiais em português brasileiro |
| http | 1.6.0 | HTTP injetável, IOClient e AbortableRequest |
| flutter_test | SDK 3.47.8; desenvolvimento | Testes unitários e de widgets sem rede |
| flutter_lints | 6.0.0; desenvolvimento | Análise estática oficial recomendada |

Não há Cupertino Icons, gerenciador de estado externo, plugins de janela,
armazenamento local ou gerador de código. ChangeNotifier/ListenableBuilder
pertencem ao SDK. `publish_to: none` impede publicação acidental do pacote.

Fontes primárias: [http](https://pub.dev/packages/http/versions/1.6.0),
[licença http](https://pub.dev/packages/http/license),
[licença Flutter](https://github.com/flutter/flutter/blob/737ba8f885dc774b53b442759999a312c9461e4e/LICENSE)
e os arquivos locais de licença da resolução abaixo.

## Cadeia efetivamente resolvida

"Execução" indica alcance no grafo das dependências de execução. Não afirma
que todo pacote ou arquivo será incorporado ao binário: por exemplo `web` é
transitivo de http, mas o aplicativo desta entrega tem somente alvo Windows.
As versões 0.0.0 no lock para pacotes do SDK seguem a versão do SDK acima.

| Pacote | Versão | Contexto | Licença conferida |
|---|---|---|---|
| async | 2.13.1 | Execução | BSD-3-Clause |
| boolean_selector | 2.1.2 | Desenvolvimento | BSD-3-Clause |
| characters | 1.4.1 | Execução | BSD-3-Clause |
| clock | 1.1.3 | Execução | Apache-2.0 |
| collection | 1.19.1 | Execução | BSD-3-Clause |
| fake_async | 1.3.3 | Desenvolvimento | Apache-2.0 |
| flutter | 0.0.0 | Execução | BSD-3-Clause (Flutter) |
| flutter_lints | 6.0.0 | Desenvolvimento | BSD-3-Clause |
| flutter_localizations | 0.0.0 | Execução | BSD-3-Clause (Flutter) |
| flutter_test | 0.0.0 | Desenvolvimento | BSD-3-Clause (Flutter) |
| http | 1.6.0 | Execução | BSD-3-Clause |
| http_parser | 4.1.2 | Execução | BSD-3-Clause |
| intl | 0.20.3 | Execução | BSD-3-Clause |
| leak_tracker | 11.0.2 | Desenvolvimento | BSD-3-Clause |
| leak_tracker_flutter_testing | 3.0.10 | Desenvolvimento | BSD-3-Clause |
| leak_tracker_testing | 3.0.2 | Desenvolvimento | BSD-3-Clause |
| lints | 6.1.0 | Desenvolvimento | BSD-3-Clause |
| matcher | 0.12.20 | Desenvolvimento | BSD-3-Clause |
| material_color_utilities | 0.13.0 | Execução | Apache-2.0 |
| meta | 1.18.3 | Execução | BSD-3-Clause |
| path | 1.9.1 | Execução | BSD-3-Clause |
| sky_engine | 0.0.0 | Execução | Avisos compostos do engine |
| source_span | 1.10.2 | Execução | BSD-3-Clause |
| stack_trace | 1.12.2 | Desenvolvimento | BSD-3-Clause |
| stream_channel | 2.1.4 | Desenvolvimento | BSD-3-Clause |
| string_scanner | 1.4.1 | Execução | BSD-3-Clause |
| term_glyph | 1.2.2 | Execução | BSD-3-Clause |
| test_api | 0.7.12 | Desenvolvimento | BSD-3-Clause |
| typed_data | 1.4.0 | Execução | BSD-3-Clause |
| vector_math | 2.4.0 | Execução | BSD-3-Clause |
| vm_service | 15.4.0 | Desenvolvimento | BSD-3-Clause |
| web | 1.1.2 | Execução | BSD-3-Clause |

## Obrigações e limites

As licenças BSD de três cláusulas exigem preservação dos avisos de copyright,
condições e disclaimer e não permitem usar nomes dos autores para endosso
indevido. Apache-2.0 exige preservação da licença e avisos aplicáveis, conferência
de NOTICE e identificação de modificações quando exigida. Essas obrigações
pertencem aos componentes de terceiros; não relicenciam o código proprietário
Desktop Messaging Lab. A triagem dos pacotes Dart permite continuar o desenvolvimento, sem
aprovar automaticamente os binários finais ou sua distribuição.

Flutter/Dart e o engine incluem terceiros além do grafo pub. O arquivo
`C:/Develop/flutter/bin/cache/pkg/sky_engine/LICENSE` contém avisos compostos;
não classificar o engine inteiro apenas pela licença principal do Flutter.
O bundle release gerado conserva `data/flutter_assets/NOTICES.Z`. Nenhum aviso
de terceiros foi removido. A auditoria do futuro instalador deverá verificar
o engine, Dart, DLLs nativas, runtimes C++, recursos e os avisos efetivamente
incluídos no conjunto distribuído.

A tipografia solicitada é Segoe UI disponível no Windows, sem copiar arquivo
da fonte para o repositório/bundle. Material Icons é recurso do SDK incorporado
por `uses-material-design`. O artefato do SDK declara CC BY 4.0; a atribuição
a Google LLC e o texto integral estão em `LICENCAS/`, incluídos como assets.
Esse aviso não apareceu no NOTICES.Z inspecionado e foi preservado separadamente.
O texto original está em
`bin/cache/artifacts/material_fonts/materialicons_license.txt`.
Preservar também essa atribuição e a indicação de tree shaking no futuro
instalador; a licença do recurso não altera a licença do código Desktop Messaging Lab.
O ícone .ico permanece o padrão gerado pelo Flutter. O uso desses recursos
não autoriza assumir propriedade dos recursos de terceiros.

Ferramentas de testes/análise não devem entrar automaticamente no instalador.
Não houve decisão de empacotamento final, assinatura, instalador ou distribuição
comercial. A autorização da Entrega 4 limita release à validação técnica.

## Reproduzir

Dentro de `frontend/`:

```powershell
flutter pub get --enforce-lockfile
flutter analyze --no-pub
flutter test --no-pub --reporter expanded
dart format --output=none --set-exit-if-changed lib test
```

Preservar o lockfile da aplicação. Atualizações de dependências são deliberadas,
com nova revisão da cadeia, licenças e testes. O aviso de versões mais recentes
incompatíveis com os constraints/SDK não motivou upgrade nesta entrega.

## Entrega 7 - configuracao nativa de build (30/10/2026)

Sem pacote Dart novo, upgrade de SDK ou plugin adicional. Debug e opt-in de build
EDISK_SOURCE_BACKEND=true utilizam a .venv; release normal utiliza o bundle.
O CMake le a configuracao gerada Flutter para incorporar essa escolha ao runner.
Scripts de desenvolvimento passam o opt-in explicitamente. O bundle ignora
variaveis de desenvolvimento herdadas e identifica o perfil no titulo nativo.

Assets, NOTICES.Z, licencas do engine/Flutter e atribuicao Material Icons continuam
preservados. CRT x64 release e copiado somente de VC/Redist, com versao registrada,
termos Microsoft e auditoria PE. Nenhum arquivo de Segoe UI e redistribuido.
app.so do Flutter Windows e um snapshot ELF x64 consumido pelo engine.

Validação source, builds e ensaios integrados frozen foram concluídos localmente.
Janela real em pasta RX, Unicode, PATH reduzido e fechamento normal/abrupto aprovados.
A captura sanitizada mostra a tela de configurar administrador no release empacotado.
Windows limpo e aprovacao juridica do conjunto continuam pendentes.
Nao ha instalador/atualizador/assinatura; nenhuma politica Windows foi alterada.

## Entrega 8 — runner, coordenador e instalação (30/10/2026)

Nenhum pacote Dart, plugin ou SDK foi acrescentado ou atualizado. O launcher e o guard usam Win32/BCrypt, C++17 e toolchain existente. O launcher tem CRT estático; o cliente não instala runtime adicional. Mantém Job externo, handles e marcador até confirmar o término dos processos. O Job interno conserva KILL_ON_JOB_CLOSE, JOB_LIST e HANDLE_LIST restrito.

CMake valida defines Flutter e versão do produto, gerando recurso PE estruturado de variante. Candidato incorpora produção; técnico instalado usa dados separados. Avisos Flutter, engine e Material Icons são preservados. Termos Microsoft e revisão jurídica permanecem pendentes.

## Entrega 9 — seleção de arquivos

Nenhuma nova dependência de execução Flutter permanece. file_selector foi
avaliado e removido após a exigência de symlinks/Modo de Desenvolvedor no ambiente.
Diálogos de abrir/salvar usam comdlg32 do Windows, via módulo file_dialogs do runner
e MethodChannel dedicado; não criam processos nem consultam SQLite.
comdlg32 pertence ao Windows e não é redistribuída. Locks anteriores preservados.
A versão técnica foi incrementada a 0.1.0+7 para distinguir o novo payload.
