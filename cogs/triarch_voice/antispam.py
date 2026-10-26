"""Примитивы защиты от спама: кулдаун и ограничитель частоты (sliding window)."""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Dict, Hashable

Clock = Callable[[], float]


class Cooldown:
    """Одно действие на ключ раз в `seconds` секунд."""

    def __init__(self, seconds: float, clock: Clock = time.monotonic) -> None:
        self._seconds = seconds
        self._clock = clock
        self._last: Dict[Hashable, float] = {}

    def remaining(self, key: Hashable) -> float:
        """Сколько секунд осталось ждать (0 — можно действовать)."""
        last = self._last.get(key)
        if last is None:
            return 0.0
        return max(0.0, self._seconds - (self._clock() - last))

    def trigger(self, key: Hashable) -> None:
        self._last[key] = self._clock()
        self._prune()

    def _prune(self) -> None:
        # Не даём словарю расти бесконечно.
        if len(self._last) < 1000:
            return
        now = self._clock()
        self._last = {k: t for k, t in self._last.items() if now - t < self._seconds}


class RateLimiter:
    """Не более `limit` действий на ключ за скользящее окно `period` секунд."""

    def __init__(self, limit: int, period: float, clock: Clock = time.monotonic) -> None:
        self._limit = limit
        self._period = period
        self._clock = clock
        self._hits: Dict[Hashable, Deque[float]] = {}

    def hit(self, key: Hashable) -> float:
        """Регистрирует попытку.

        Возвращает 0, если действие разрешено, иначе — сколько секунд подождать.
        Отклонённые попытки не учитываются, чтобы спам не продлевал блокировку бесконечно.
        """
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._period:
            hits.popleft()

        if len(hits) >= self._limit:
            return self._period - (now - hits[0])

        hits.append(now)
        self._prune(now)
        return 0.0

    def _prune(self, now: float) -> None:
        if len(self._hits) < 1000:
            return
        self._hits = {key: hits for key, hits in self._hits.items() if hits and now - hits[-1] < self._period}

    def reset(self, key: Hashable) -> None:
        self._hits.pop(key, None)
