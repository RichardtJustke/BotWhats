import aiomysql

from app.core.resources import get_mysql_pool


async def find_by_phone(phone: str) -> dict | None:
    digits = "".join(c for c in phone if c.isdigit())
    ultimos8 = digits[-8:]  # ignora país, DDD, e o "9" extra de celular, se houver

    async with get_mysql_pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT * FROM beneficiarios
                WHERE RIGHT(REPLACE(celular, '-', ''), 8) = %s
                   OR RIGHT(REPLACE(telefone, '-', ''), 8) = %s
                LIMIT 1
                """,
                (ultimos8, ultimos8),
            )
            return await cur.fetchone()


async def find_by_cpf(cpf: str) -> dict | None:
    digits = "".join(c for c in cpf if c.isdigit())

    async with get_mysql_pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT * FROM beneficiarios
                WHERE REPLACE(REPLACE(cpf, '.', ''), '-', '') = %s
                LIMIT 1
                """,
                (digits,),
            )
            return await cur.fetchone()
