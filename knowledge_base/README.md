# knowledge_base/

Artigos de exemplo de uma administradora de benefícios fictícia, em Markdown.
São a única fonte que o bot usa para responder dúvidas (RAG). O índice está em
[`index.md`](index.md). As operadoras (Vida Plena Saúde, Horizonte Saúde,
Sorriso Odonto) e os contatos são fictícios: troque pelos reais ao usar.

## Placeholders

Os dados da empresa ficam no `.env` e são inseridos na indexação:

| Placeholder | Variável |
|---|---|
| `{{empresa}}` | `COMPANY_NAME` |
| `{{app}}` | `COMPANY_APP_NAME` |
| `{{email_atendimento}}` | `SUPPORT_EMAIL` |
| `{{email_ouvidoria}}` | `OMBUDSMAN_EMAIL` |
| `{{telefone}}` | `SUPPORT_PHONE` |
| `{{whatsapp}}` | `SUPPORT_WHATSAPP` |

## Organização

Uma pasta por tema. O nome da pasta vira o metadado `tema` na indexação.

| Pasta | Assunto |
|---|---|
| `atendimento/` | canais, prazos, app, papel da administradora |
| `boletos/` | 2ª via, formas de pagamento, atraso, valores |
| `cancelamento/` | cancelamento e recontratação (**não vai para o LLM**, ver abaixo) |
| `carencias/` | carências |
| `contrato/` | regras do contrato |
| `operadoras/` | canais e informações de cada operadora |
| `ouvidoria/` | ouvidoria |
| `reajuste/` | reajuste anual e por faixa etária, coparticipação |
| `telemedicina/` | telemedicina |
| `outros/` | demais assuntos |

> A pasta `cancelamento/` é indexada, mas o RAG a exclui da busca: o bot
> nunca explica cancelamento, ele encaminha. O texto de cancelamento pelo app
> fora do horário fica fixo em `app/services/escalation.py`. Se o artigo mudar,
> atualize lá também.

## Formato de um artigo

```markdown
---
titulo: "Como solicitar o cancelamento do plano pelo aplicativo?"
categoria: "Cancelamento"
publico: beneficiario
slug: como-solicitar-o-cancelamento-pelo-aplicativo
tags: [cancelamento]
atualizado_em: 2026-08-18
---

# Pergunta do cliente

Resposta...

## Subtópico
...
```

- Um assunto por arquivo, escrito como resposta à pergunta real do cliente.
- A indexação quebra o texto em cada `## `. Use subtítulos para separar
  assuntos, para que cada trecho faça sentido sozinho.
- Telefones, e-mails e links devem estar exatamente corretos: o bot copia
  caractere por caractere e bloqueia telefones que não estão no texto.
- Dado que muda por cliente (boleto, contrato, carteirinha) não entra aqui:
  vem das integrações (MySQL, boleto-service).

## Depois de editar

```bash
uv run python scripts/ingest.py
```

Só os trechos alterados são reindexados. Em produção, o ingest roda no
próximo deploy ou restart do container.
