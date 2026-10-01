# app/services/

Regras de negócio do bot. Cada arquivo cuida de um assunto; o
[webhook](../api/README.md) chama esses serviços na ordem certa.

## Por assunto

### Cliente e identificação
| Arquivo | O que faz |
|---|---|
| [`clients.py`](clients.py) | acha/cria o cliente no Mongo pelo telefone e atualiza os campos de identidade. `IDENTITY_FIELDS` lista o que é apagado a cada sessão nova. |
| [`beneficiarios.py`](beneficiarios.py) | busca no MySQL (`beneficiarios`) por telefone (últimos 8 dígitos) ou por CPF. |
| [`identification.py`](identification.py) | máquina de estados da identificação: sem estado → `awaiting_confirmation` ("É você?") → `confirmed`, ou `visitor`. Não é barreira: quem não é achado segue como visitante. Ao confirmar, consulta os boletos e monta o contexto do cliente para o LLM. |

### Boletos
| Arquivo | O que faz |
|---|---|
| [`boletos.py`](boletos.py) | chama `GET /faturas/{cpf}` no boleto-service, separa vencidos e a vencer (data de Brasília), monta a mensagem com os links e o resumo "Situação financeira" que vai para o prompt. Devolve `None` se o serviço falhar ou não estiver configurado, e o atendimento segue normal. |

A consulta usa sempre o CPF **confirmado**, nunca um CPF digitado na mensagem.

### Resposta com IA
| Arquivo | O que faz |
|---|---|
| [`rag.py`](rag.py) | busca os 3 trechos mais parecidos no Chroma (sem o tema `cancelamento`), monta o prompt (`SYSTEM_PROMPT_BASE`) e chama o OpenRouter com fallback entre os modelos de `OPENROUTER_MODELS`. Troca por resposta segura qualquer telefone que não esteja no contexto. |
| [`summaries.py`](summaries.py) | `summary_watcher`: resume em background os atendimentos encerrados de beneficiários confirmados. O resumo só volta no prompt quando o **mesmo CPF** for confirmado de novo. |

### Escalonamento e sessão
| Arquivo | O que faz |
|---|---|
| [`escalation.py`](escalation.py) | detecta por regex (sem acento) cancelamento, carta de permanência, pedido de atendente e ofensa; textos de encaminhamento; resposta de cancelamento fora do horário (app ou e-mail do odontológico); leitura da marcação `[TRANSFERIR]` do LLM; registro em `escalations`. |
| [`business_hours.py`](business_hours.py) | `is_business_hours()`: se há atendente agora, conforme `ATENDIMENTO_DIAS`/`INICIO`/`FIM`, no fuso de Brasília. Define `BRASILIA`. |
| [`session.py`](session.py) | ciclo da sessão (`active` → `awaiting_more_help` → `closed`, ou `escalated`) e o `session_watcher`, que manda "posso ajudar em algo mais?" e encerra por inatividade. O diagrama está no topo do arquivo. |
| [`conversation.py`](conversation.py) | salva e lê o histórico (`messages`), reconhece o eco das mensagens do bot e faz a idempotência dos eventos. |

### WhatsApp e extras
| Arquivo | O que faz |
|---|---|
| [`openwa.py`](openwa.py) | chamadas à API do OpenWA: enviar texto, enviar imagem por URL, resolver o telefone de um contato `@lid`. |
| [`easter_eggs.py`](easter_eggs.py) | "gato", "passarinho" e "pokemon" respondem com imagem, sem passar pelo LLM. |

## Onde mexer para…

- **mudar o que o bot fala ou pode falar**: `SYSTEM_PROMPT_BASE` em `rag.py`;
- **nova palavra-chave que manda para humano**: padrões em `escalation.py`;
- **mudar a mensagem de confirmação ou de boletos**: `identification.py` / `boletos.py`;
- **tempos de sessão e horário de atendimento**: variáveis no `.env`, não no código.

## `source` das mensagens salvas

`llm` (gerada pelo modelo), `system` (texto fixo do código), `human`
(atendente pelo número da empresa) e `user` (cliente).
