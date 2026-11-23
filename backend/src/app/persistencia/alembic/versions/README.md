Nenhuma revisão na Entrega 3. A tabela técnica `alembic_version`, vazia,
representa `base`. Criar a primeira revisão somente com uma mudança real
autorizada. Não reescrever revisões aplicadas nem criar tabelas fora do Alembic.

Execução operacional passa pelo lifespan, com exclusividade, inspeção e snapshot.
O template é ferramenta de desenvolvimento; os recursos deste diretório devem
ser incluídos no futuro executável e validados no empacotamento.

Entrega 6: `0001_autenticacao_local` é a primeira revisão efetiva, com
`down_revision=None`. Cria somente usuários, sessões e marcador de configuração.
Não reescrever após aplicação/distribuição. Downgrade remove os dados de acesso
e só é exercitado em testes descartáveis. O env usa metadata ORM sem create_all.
