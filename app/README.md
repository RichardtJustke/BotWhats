# app/

Código da API do chat-service (FastAPI). Visão geral do projeto no
[README principal](../README.md).

| Pasta / arquivo | O que tem |
|---|---|
| [`main.py`](main.py) | cria o app, abre e fecha as conexões no `lifespan`, inicia as tarefas de fundo e expõe `GET /health` |
| [`api/`](api/README.md) | rotas HTTP (hoje só o webhook do OpenWA) |
| [`core/`](core/README.md) | configuração, conexões compartilhadas e validação de assinatura |
| [`models/`](models/README.md) | modelos Pydantic dos payloads recebidos |
| [`services/`](services/README.md) | regras de negócio: identificação, RAG, sessão, escalonamento, boletos |

## Ciclo de vida

No startup, `main.py`:

1. chama `init_resources()` ([core/resources.py](core/resources.py)): Mongo,
   pool do MySQL, cliente HTTP e coleção do Chroma;
2. testa Mongo e MySQL (se falhar, a API não sobe);
3. inicia `session_watcher` e `summary_watcher` em background.

No shutdown, cancela as tarefas e fecha as conexões.

## Convenções

- Os serviços nunca criam conexão própria: pegam o recurso pronto pelos
  getters de `core/resources.py` (`get_db()`, `get_mysql_pool()`,
  `get_http_client()`, `get_knowledge_collection()`).
- Toda regra que não pode falhar (cancelamento, identidade, boletos) é
  determinística, em código. O LLM só entra para redigir respostas de dúvidas.
