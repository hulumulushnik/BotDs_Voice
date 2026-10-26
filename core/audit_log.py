"""Отправка служебных событий в текстовый лог-канал сервера."""

from __future__ import annotations

import logging
from typing import Iterable, Sequence, Tuple

import discord

logger = logging.getLogger(__name__)

Field = Tuple[str, str]

_EMBED_FIELD_LIMIT = 1024
_EMBED_DESCRIPTION_LIMIT = 4096


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class AuditLog:
    """Пишет embed-сообщения в канал логов каждого сервера.

    Канал ищется по имени при каждой отправке, поэтому переименование
    или пересоздание канала не требует перезапуска бота.
    """

    def __init__(self, channel_name: str) -> None:
        self._channel_name = channel_name

    def find_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel = discord.utils.get(guild.text_channels, name=self._channel_name)
        if channel is None:
            return None
        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.embed_links):
            logger.warning("Нет прав на отправку embed в #%s (%s)", channel.name, guild.name)
            return None
        return channel

    async def send(
        self,
        guild: discord.Guild,
        title: str,
        description: str = "",
        color: discord.Color = discord.Color.blue(),
        fields: Sequence[Field] = (),
        *,
        system: bool = False,
    ) -> None:
        channel = self.find_channel(guild)
        if channel is None:
            return

        embed = discord.Embed(
            title=f"[СИСТЕМА] {title}" if system else title,
            description=_truncate(description, _EMBED_DESCRIPTION_LIMIT),
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        for name, value in fields:
            embed.add_field(name=name, value=_truncate(value or "—", _EMBED_FIELD_LIMIT), inline=True)

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            logger.warning("Не удалось отправить лог в #%s: %s", channel.name, exc)

    async def broadcast(
        self,
        guilds: Iterable[discord.Guild],
        title: str,
        description: str = "",
        color: discord.Color = discord.Color.blue(),
        fields: Sequence[Field] = (),
    ) -> None:
        """Системное сообщение во все серверы, где есть лог-канал."""
        for guild in guilds:
            await self.send(guild, title, description, color, fields, system=True)
