"""Horário de atendimento humano (fuso de Brasília)."""

from datetime import datetime, time, timedelta, timezone

from app.core import config

# Container roda em UTC. Brasília não tem horário de verão desde 2019.
BRASILIA = timezone(timedelta(hours=-3))


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


_INICIO = _parse_hhmm(config.ATENDIMENTO_INICIO)
_FIM = _parse_hhmm(config.ATENDIMENTO_FIM)


def is_business_hours(now: datetime | None = None) -> bool:
    """Tem atendente humano agora? Feriados não são considerados."""
    now = (now or datetime.now(BRASILIA)).astimezone(BRASILIA)
    return now.weekday() in config.ATENDIMENTO_DIAS and _INICIO <= now.time() < _FIM
