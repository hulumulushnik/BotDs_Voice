"""Наборы прав (overwrites) для временных комнат и помощник для их частичного изменения."""

from __future__ import annotations

from typing import Union

import discord

Target = Union[discord.Role, discord.Member]

# Владелец получает только доступ в комнату (даже закрытую).
# Модерация выполняется ботом через панель: прямые права manage_channels /
# move_members / mute_members позволили бы обходить антиспам и выдавать
# серверный мут, который бот потом не смог бы снять.
OWNER_PERMS = dict(view_channel=True, connect=True, speak=True, stream=True)
OWNER_RESET = {key: None for key in OWNER_PERMS}

BOT_PERMS = dict(
    view_channel=True,
    connect=True,
    send_messages=True,
    embed_links=True,
    manage_channels=True,
    move_members=True,
    mute_members=True,
)

NO_ACCESS = dict(view_channel=False, connect=False)
ACCESS_RESET = dict(view_channel=None, connect=None)

VOICE_MUTED = dict(speak=False, stream=False)
VOICE_UNMUTED = dict(speak=None, stream=None)


async def update_overwrite(
    channel: discord.abc.GuildChannel,
    target: Target,
    *,
    reason: str | None = None,
    **perms: bool | None,
) -> None:
    """Меняет только переданные права, сохраняя остальные.

    Если после изменения overwrite пуст — удаляет его целиком.
    """
    overwrite = channel.overwrites_for(target)
    overwrite.update(**perms)
    await channel.set_permissions(target, overwrite=None if overwrite.is_empty() else overwrite, reason=reason)
