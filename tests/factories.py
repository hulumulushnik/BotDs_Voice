"""Лёгкие заглушки объектов discord.py для юнит-тестов."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from config import AntiSpamConfig, BotConfig


def make_config(**antispam) -> BotConfig:
    spam = dict(
        room_create_cooldown=30,
        max_rooms_per_guild=2,
        panel_actions=3,
        panel_period=10,
        rename_actions=2,
        rename_period=600,
        raise_cooldown=60,
    )
    spam.update(antispam)
    return BotConfig(
        token="x",
        trigger_channel_name="➕ Создать комнату",
        log_channel_name="логи",
        room_name_template="🏛 | {owner}",
        unverified_role_name="Не верифицирован",
        muted_role_name="Заглушён",
        data_file=Path(tempfile.mkdtemp()) / "rooms.json",
        cleanup_interval=30,
        antispam=AntiSpamConfig(**spam),
    )


def make_guild(guild_id: int = 1, roles=()):
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.roles = list(roles)
    guild.default_role = MagicMock(name="@everyone")
    guild.me = MagicMock(name="bot")
    guild.get_channel = MagicMock(return_value=None)
    guild.get_member = MagicMock(return_value=None)
    guild.voice_channels = []
    return guild


def make_member(member_id: int, guild, *, bot=False, roles=(), moderator=False):
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.bot = bot
    member.guild = guild
    member.roles = list(roles)
    member.display_name = f"user{member_id}"
    member.mention = f"<@{member_id}>"
    member.guild_permissions = discord.Permissions(administrator=moderator)
    member.move_to = AsyncMock()
    return member


def make_voice_channel(channel_id: int, guild, members=()):
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.guild = guild
    channel.members = list(members)
    channel.name = f"room{channel_id}"
    channel.category = None
    channel.delete = AsyncMock()
    channel.edit = AsyncMock()
    channel.set_permissions = AsyncMock()
    channel.overwrites_for = MagicMock(return_value=discord.PermissionOverwrite())
    return channel


def make_role(name: str):
    return SimpleNamespace(name=name)
