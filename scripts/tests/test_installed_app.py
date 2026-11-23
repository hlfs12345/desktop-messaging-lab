"""Entrada de compatibilidade para o ensaio real A-B do protocolo 2.

Use --artifact CAMINHO_A --artifact-b CAMINHO_B (ou --a / --b).
Os relatórios anteriores de reinstalação da mesma versão permanecem históricos.
"""

from test_real_upgrade import main

if __name__ == "__main__":
    main()
