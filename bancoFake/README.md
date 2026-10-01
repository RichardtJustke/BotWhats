# bancoFake/

MySQL de mentira com beneficiários fictícios, para rodar o bot localmente
sem acesso ao banco real. **Não é usado em produção.**

| Arquivo | O que faz |
|---|---|
| [`schema.sql`](schema.sql) | cria o banco `beneficiarios` e a tabela com as colunas do cadastro |
| [`seed.sql`](seed.sql) | insere os beneficiários de exemplo (alguns titulares com dependentes, alguns suspensos ou cancelados) |

## Subindo

```bash
docker run -d --name chat-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root -e MYSQL_USER=chatservice -e MYSQL_PASSWORD=chatservice \
  mysql:8
docker exec -i chat-mysql mysql -uroot -proot < bancoFake/schema.sql
docker exec -i chat-mysql mysql -uroot -proot < bancoFake/seed.sql
docker exec -i chat-mysql mysql -uroot -proot \
  -e "GRANT SELECT ON beneficiarios.* TO 'chatservice'@'%';"
```

No `.env`: `MYSQL_HOST=localhost`, `MYSQL_USER=chatservice`,
`MYSQL_PASSWORD=chatservice`, `MYSQL_DB=beneficiarios`.

Para o bot achar você pelo telefone, coloque o seu número (com DDD) na
coluna `celular` de algum registro. Ele compara só os últimos 8 dígitos.

Colunas que o bot usa: `cpf`, `celular`, `telefone`, `nome_beneficiario`,
`plano`, `status`, `operadora`.
