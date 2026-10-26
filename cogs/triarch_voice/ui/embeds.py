"""Построение embed-сообщения панели."""

from __future__ import annotations

import discord

from ..service import is_locked
from .layout import ALL_BUTTONS


def _legend() -> str:
    """Описание кнопок столбиком: одна кнопка — одна строка."""
    return "\n".join(f"{b.emoji} — {b.label}" for b in ALL_BUTTONS)


def build_panel_embed(channel: discord.VoiceChannel, owner_id: int | None) -> discord.Embed:
    limit = channel.user_limit
    embed = discord.Embed(title="Triarch Control Panel", color=discord.Color.dark_gold())
    embed.add_field(name="Владелец", value=f"<@{owner_id}>" if owner_id else "—", inline=True)
    embed.add_field(name="Доступ", value="🔒 Закрыта" if is_locked(channel) else "🔓 Открыта", inline=True)
    embed.add_field(name="Лимит", value=str(limit) if limit else "Без лимита", inline=True)
    embed.add_field(name="Кнопки", value=_legend(), inline=False)
    embed.set_footer(
        text="Если владелец выйдет, комната перейдёт к следующему участнику. Пустая комната удаляется автоматически."
    )
    return embed
