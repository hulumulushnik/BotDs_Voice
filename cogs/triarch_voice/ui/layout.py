"""Декларативная сетка кнопок панели управления.

Чтобы поменять расположение, порядок или стиль кнопок — правьте только PANEL_GRID.
Discord допускает до 5 рядов по 5 кнопок.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import discord

CUSTOM_ID_PREFIX = "triarch:panel:"


@dataclass(frozen=True)
class PanelButton:
    key: str  # идентификатор действия (часть custom_id — не менять у работающего бота)
    emoji: str
    label: str  # подпись в легенде embed
    style: discord.ButtonStyle = discord.ButtonStyle.secondary

    @property
    def custom_id(self) -> str:
        return CUSTOM_ID_PREFIX + self.key


_S = discord.ButtonStyle

# Верхний ряд — настройки комнаты (4), нижний — участники (3).
PANEL_GRID: Tuple[Tuple[PanelButton, ...], ...] = (
    (
        PanelButton("rename", "✏️", "Изменить название"),
        PanelButton("lock", "🔒", "Закрыть / открыть комнату"),
        PanelButton("limit", "👥", "Лимит участников"),
        PanelButton("raise", "⬆️", "Поднять комнату наверх", _S.success),
    ),
    (
        PanelButton("mute", "🔇", "Мут / размут участника", _S.danger),
        PanelButton("ban", "🔨", "Бан / разбан участника", _S.danger),
        PanelButton("transfer", "👑", "Передать владение", _S.primary),
    ),
)

ALL_BUTTONS: Tuple[PanelButton, ...] = tuple(button for row in PANEL_GRID for button in row)
