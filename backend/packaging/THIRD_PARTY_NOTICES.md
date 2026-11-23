# Desktop Messaging Lab - artefato experimental, nao comercial

O codigo Desktop Messaging Lab permanece proprietario. Este artefato nao constitui aprovacao
juridica ou comercial da distribuicao. Licencas de terceiros aplicam-se aos
respectivos componentes, sem relicenciar o codigo Desktop Messaging Lab.

LICENSES/components.json relaciona versoes, declaracoes de licenca e textos
copiados das distribuicoes Python de execucao. Os textos preservam copyrights,
condicoes e disclaimers. MIT/BSD exigem preservar avisos; Apache exige tambem
NOTICE aplicavel. Greenlet inclui MIT e PSF. Os metadados nao substituem a
auditoria do codigo nativo e dos terceiros incorporados.

Python: LICENSES/Python-LICENSE.txt, incluindo avisos de terceiros da distribuicao.
O bundle inclui Python, SQLite, OpenSSL, libffi e extensoes dos wheels.
Argon2 bindings: MIT; Argon2 incorporado: CC0 ou Apache-2.0, com texto integral
em LICENSES/native/argon2-LICENSE.txt. Rotinas de Thomas Pornin e Samuel Neves:
avisos CC0 descritos no inventario do backend.
CFFI: MIT-0; libffi incorporado: copyright Red Hat e MIT preservados separadamente.
Pydantic-core: MIT; revisao complementar dos crates incorporados permanece pendente.

Flutter/Dart/engine: LICENSES/Flutter-LICENSE.txt e Engine-LICENSE.txt;
data/flutter_assets/NOTICES.Z preservado. Material Icons: Google LLC, CC BY 4.0;
texto e atribuicao em data/flutter_assets/LICENCAS, fonte reduzida por tree shaking.
Segoe UI e utilizada do Windows e nao e copiada para o bundle.

PyInstaller 6.22.3: GPL com excecao do bootloader que permite gerar bundles
proprietarios. Texto integral preservado em LICENSES/PyInstaller-COPYING.txt.
Hooks e ferramentas permanecem no ambiente de build; nao sao ferramentas entregues.

Microsoft: DLLs release x64 obtidas de VC/Redist do Visual Studio.
LICENSES/native contem a lista oficial de redistribuicao e os termos Community
2026 (texto extraido do documento oficial, sem alterar as palavras).
A origem e os hashes sao registrados em sources.json. Direito de uso da edicao,
licenca valida e cumprimento das condicoes de redistribuicao devem ser revistos
pelo proprietario antes da distribuicao comercial. Binarios nao sao modificados.

Pendencias: revisao juridica do conjunto final, crates transitivos de binarios
Rust, componentes nativos compostos do engine e aplicabilidade dos termos do
toolchain ao titular. Geracao com sucesso nao encerra essas pendencias.

Python 3.14.10 Windows incorpora OpenSSL 3.5.9 (copyright 1998-2026 The OpenSSL
Authors, Apache-2.0), zlib-ng 2.2.4 (zlib), mpdecimal 4.0.0 (BSD) e xz 5.2.5.
Textos especificos preservados em LICENSES/native; os utilitarios xz e seus
componentes de build nao integram o artefato. liblzma usado pelo modulo _lzma:
dominio publico conforme COPYING upstream. bzip2, libffi e zstd tem avisos
tambem no LICENSE do Python. SQLite 3.50.4 e dominio publico:
https://sqlite.org/copyright.html.

Entrega 9: phonenumbers 9.0.42 Apache-2.0; openpyxl 3.1.5 MIT; et-xmlfile 2.0.0
MIT com avisos PSF de código incorporado; defusedxml 0.7.1 PSFL. Textos completos
dos wheels acompanham LICENSES/components.json, inclusive LICENCE.python.
Não se altera a licença proprietária do Desktop Messaging Lab. Nenhum Office/Excel, pandas,
NumPy, Node ou runtime extra é necessário no cliente.
