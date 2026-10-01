# app/core/

Infraestrutura compartilhada. Nada aqui tem regra de negócio.

| Arquivo | O que faz |
|---|---|
| [`config.py`](config.py) | lê o `.env` e expõe as variáveis como constantes. Falha no import se faltar `OPENWA_API_KEY`, `OPENROUTER_API_KEY` ou `MYSQL_PASSWORD`. A lista completa de variáveis está no [README principal](../../README.md#configuração). |
| [`resources.py`](resources.py) | cria no startup e fecha no shutdown: cliente do Mongo (e os índices), pool do MySQL, cliente HTTP único (`httpx`, timeout padrão 10s) e a coleção `knowledge_base` do Chroma. Os serviços acessam tudo pelos getters `get_db()`, `get_mysql_pool()`, `get_http_client()` e `get_knowledge_collection()`. |
| [`security.py`](security.py) | `verify_signature`: confere o HMAC-SHA256 (`sha256=<hex>`) que o OpenWA manda no webhook, com comparação em tempo constante. |

## Índices do Mongo

`resources.py` garante os índices a cada startup (`create_index` é
idempotente). Se criar uma consulta nova em cima de uma coleção grande,
adicione o índice lá.
