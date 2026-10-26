"""Загрузка конфигурации бота из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Некорректная или неполная конфигурация."""


@dataclass(frozen=True)
class AntiSpamConfig:
    room_create_cooldown: int  # сек. между созданиями комнаты одним пользователем
    max_rooms_per_guild: int  # верхний предел временных комнат на сервере
    panel_actions: int  # сколько нажатий на панель допускается...
    panel_period: int  # ...за этот период (сек.)
    rename_actions: int  # переименований одной комнаты...
    rename_period: int  # ...за этот период (у Discord лимит 2 / 10 мин)
    raise_cooldown: int  # сек. между подъёмами одной комнаты наверх


@dataclass(frozen=True)
class BotConfig:
    token: str
    trigger_channel_name: str
    log_channel_name: str
    room_name_template: str
    unverified_role_name: str
    muted_role_name: str
    data_file: Path
    cleanup_interval: int
    antispam: AntiSpamConfig


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом, получено: {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} должно быть >= {minimum}, получено: {value}")
    return value


def load_config() -> BotConfig:
    load_dotenv()

    token = _get_str("DISCORD_TOKEN", "")
    if not token:
        raise ConfigError("Не указан DISCORD_TOKEN в .env")

    template = _get_str("ROOM_NAME_TEMPLATE", "🏛 | {owner}")
    if "{owner}" not in template:
        raise ConfigError("ROOM_NAME_TEMPLATE должен содержать плейсхолдер {owner}")

    return BotConfig(
        token=token,
        trigger_channel_name=_get_str("TRIGGER_CHANNEL_NAME", "➕ Создать комнату"),
        log_channel_name=_get_str("LOG_CHANNEL_NAME", "логи-бота"),
        room_name_template=template,
        unverified_role_name=_get_str("UNVERIFIED_ROLE_NAME", "Не верифицирован"),
        muted_role_name=_get_str("MUTED_ROLE_NAME", "Заглушён"),
        data_file=Path(_get_str("DATA_FILE", "data/rooms.json")),
        cleanup_interval=_get_int("CLEANUP_INTERVAL", 30, minimum=5),
        antispam=AntiSpamConfig(
            room_create_cooldown=_get_int("ROOM_CREATE_COOLDOWN", 30),
            max_rooms_per_guild=_get_int("MAX_ROOMS_PER_GUILD", 50, minimum=1),
            panel_actions=_get_int("PANEL_RATE_ACTIONS", 5, minimum=1),
            panel_period=_get_int("PANEL_RATE_PERIOD", 10, minimum=1),
            rename_actions=_get_int("RENAME_RATE_ACTIONS", 2, minimum=1),
            rename_period=_get_int("RENAME_RATE_PERIOD", 600, minimum=1),
            raise_cooldown=_get_int("RAISE_COOLDOWN", 60),
        ),
    )
