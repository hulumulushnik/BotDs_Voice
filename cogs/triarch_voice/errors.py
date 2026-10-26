"""Исключения бизнес-логики. Текст исключения показывается пользователю как есть."""

from __future__ import annotations

import math


class TriarchError(Exception):
    """Базовая ошибка с понятным пользователю сообщением."""


class AccessDenied(TriarchError):
    """У пользователя нет права на действие."""


class InvalidTarget(TriarchError):
    """Действие нельзя применить к выбранному участнику."""


class InvalidInput(TriarchError):
    """Некорректный ввод (имя комнаты, лимит и т.п.)."""


class RoomNotFound(TriarchError):
    def __init__(self) -> None:
        super().__init__("Эта комната больше не управляется ботом.")


class RateLimited(TriarchError):
    def __init__(self, retry_after: float, what: str = "Слишком часто") -> None:
        self.retry_after = retry_after
        super().__init__(f"⏳ {what}. Повторите через {math.ceil(retry_after)} сек.")
