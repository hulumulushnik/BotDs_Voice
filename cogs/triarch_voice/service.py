"""Бизнес-логика временных голосовых комнат Triarch.

Сервис не знает про UI: он либо выполняет действие, либо бросает
TriarchError с текстом для пользователя.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, List, Set, Tuple

import discord

from config import BotConfig

from . import permissions as perms
from .antispam import Cooldown, RateLimiter
from .errors import AccessDenied, InvalidInput, InvalidTarget, RateLimited, RoomNotFound
from .repository import TriarchRepository

logger = logging.getLogger(__name__)

MAX_CHANNEL_NAME = 100
MAX_USER_LIMIT = 99
RENAME_TIMEOUT = 5.0
EMPTY_ROOM_GRACE = 1.0  # сек. — даём Discord обновить список участников

_LINK_RE = re.compile(r"(https?://|www\.|discord(?:app)?\.(?:gg|com/invite))", re.IGNORECASE)

ChannelGetter = Callable[[int], discord.abc.GuildChannel | None]


def sanitize_room_name(raw: str) -> str:
    """Нормализует пробелы, запрещает ссылки, обрезает до лимита Discord."""
    name = " ".join(raw.split())
    if not name:
        raise InvalidInput("Название не может быть пустым.")
    if _LINK_RE.search(name):
        raise InvalidInput("Ссылки в названии комнаты запрещены.")
    return name[:MAX_CHANNEL_NAME]


def human_members(channel: discord.VoiceChannel) -> List[discord.Member]:
    return [m for m in channel.members if not m.bot]


def is_locked(channel: discord.VoiceChannel) -> bool:
    return channel.overwrites_for(channel.guild.default_role).connect is False


def is_voice_muted(channel: discord.VoiceChannel, member: discord.Member) -> bool:
    return channel.overwrites_for(member).speak is False


class TriarchService:
    def __init__(self, repository: TriarchRepository, config: BotConfig) -> None:
        self.repository = repository
        self.config = config
        spam = config.antispam
        self._create_cooldown = Cooldown(spam.room_create_cooldown)
        self._panel_limiter = RateLimiter(spam.panel_actions, spam.panel_period)
        self._rename_limiter = RateLimiter(spam.rename_actions, spam.rename_period)
        self._raise_cooldown = Cooldown(spam.raise_cooldown)
        self._creating: Set[Tuple[int, int]] = set()  # (guild_id, user_id) в процессе создания

    # ------------------------------------------------------------------ роли

    def _role(self, guild: discord.Guild, name: str) -> discord.Role | None:
        return discord.utils.get(guild.roles, name=name)

    def is_user_muted(self, member: discord.Member) -> bool:
        role = self._role(member.guild, self.config.muted_role_name)
        return role is not None and role in member.roles

    def is_user_unverified(self, member: discord.Member) -> bool:
        role = self._role(member.guild, self.config.unverified_role_name)
        return role is not None and role in member.roles

    @staticmethod
    def is_moderator(member: discord.Member) -> bool:
        """Модераторы сервера: им нельзя навредить через панель, и они могут управлять любой комнатой."""
        p = member.guild_permissions
        return p.administrator or p.manage_channels or p.moderate_members

    def ensure_can_use_rooms(self, member: discord.Member, action: str = "управлять комнатами") -> None:
        if self.is_user_muted(member):
            raise AccessDenied(f"Заглушённые пользователи не могут {action}.")
        if self.is_user_unverified(member):
            raise AccessDenied(f"Неверифицированные пользователи не могут {action}.")

    # ------------------------------------------------------------ антиспам

    def check_panel_rate(self, user_id: int) -> None:
        retry = self._panel_limiter.hit(user_id)
        if retry:
            raise RateLimited(retry, "Слишком много нажатий")

    # ------------------------------------------------------- доступ к комнате

    def require_room(self, channel: discord.abc.GuildChannel | None) -> discord.VoiceChannel:
        if not isinstance(channel, discord.VoiceChannel) or not self.repository.is_temp_room(channel.id):
            raise RoomNotFound()
        return channel

    def require_manager(self, channel: discord.VoiceChannel, member: discord.Member) -> None:
        if self.repository.get_owner_id(channel.id) == member.id or self.is_moderator(member):
            return
        raise AccessDenied("Только владелец комнаты может это сделать.")

    def validate_target(
        self,
        channel: discord.VoiceChannel,
        actor: discord.Member,
        target: discord.Member,
        *,
        must_be_in_room: bool,
    ) -> None:
        if target.bot:
            raise InvalidTarget("Нельзя применить к боту.")
        if target.id == actor.id:
            raise InvalidTarget("Нельзя применить к себе.")
        if self.is_moderator(target):
            raise InvalidTarget("Нельзя применить к модератору сервера.")
        if must_be_in_room and target not in channel.members:
            raise InvalidTarget("Пользователь не в этой комнате.")

    # ------------------------------------------------------ жизненный цикл

    async def create_room(
        self, member: discord.Member, trigger_channel: discord.VoiceChannel
    ) -> discord.VoiceChannel | None:
        """Создаёт новую комнату. None — если создание для этого пользователя уже идёт.

        Старая комната пользователя к этому моменту уже передана следующему участнику
        или удалена (cog обрабатывает выход из неё раньше, чем вход в триггер).
        Бросает TriarchError при отказе.
        """
        self.ensure_can_use_rooms(member, "создавать комнаты")
        guild = member.guild

        key = (guild.id, member.id)
        if key in self._creating:
            return None  # повторное событие во время создания — игнорируем

        retry = self._create_cooldown.remaining(key)
        if retry:
            raise RateLimited(retry, "Комнату можно создавать не так часто")

        limit = self.config.antispam.max_rooms_per_guild
        if len(self.repository.rooms_in_guild(guild.id)) >= limit:
            raise AccessDenied(f"На сервере уже максимум комнат ({limit}). Попробуйте позже.")

        self._creating.add(key)
        try:
            channel = await guild.create_voice_channel(
                name=self._default_room_name(member),
                category=trigger_channel.category,
                overwrites=self._initial_overwrites(member),
                reason=f"Triarch: создание комнаты для {member}",
            )
        finally:
            self._creating.discard(key)

        self._create_cooldown.trigger(key)
        self.repository.register_room(channel.id, guild.id, member.id)
        return channel

    def _default_room_name(self, member: discord.Member) -> str:
        name = self.config.room_name_template.format(owner=member.display_name)
        return " ".join(name.split())[:MAX_CHANNEL_NAME]

    def _initial_overwrites(self, owner: discord.Member) -> dict:
        guild = owner.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True),
            guild.me: discord.PermissionOverwrite(**perms.BOT_PERMS),
            owner: discord.PermissionOverwrite(**perms.OWNER_PERMS),
        }
        for role_name in (self.config.unverified_role_name, self.config.muted_role_name):
            role = self._role(guild, role_name)
            if role:
                overwrites[role] = discord.PermissionOverwrite(**perms.NO_ACCESS)
        return overwrites

    async def delete_if_empty(self, channel: discord.VoiceChannel, *, grace: float = EMPTY_ROOM_GRACE) -> bool:
        """Удаляет комнату, если в ней нет людей. Возвращает True, если удалили."""
        if human_members(channel):
            return False
        if grace:
            await asyncio.sleep(grace)
            if human_members(channel) or not self.repository.is_temp_room(channel.id):
                return False

        self.repository.remove_room(channel.id)
        try:
            await channel.delete(reason="Triarch: комната опустела")
        except discord.NotFound:
            return False
        except discord.HTTPException as exc:
            logger.error("Ошибка удаления канала %s: %s", channel.name, exc)
            return False
        logger.info("Комната #%s удалена (пустая)", channel.name)
        return True

    async def cleanup(self, get_channel: ChannelGetter) -> List[discord.VoiceChannel]:
        """Фоновая уборка: забывает удалённые каналы и удаляет пустые комнаты."""
        deleted: List[discord.VoiceChannel] = []
        for channel_id in self.repository.room_ids():
            channel = get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                self.repository.remove_room(channel_id)
                continue
            if await self.delete_if_empty(channel, grace=0):
                deleted.append(channel)
        return deleted

    def forget_room(self, channel_id: int) -> None:
        self.repository.remove_room(channel_id)

    # ---------------------------------------------------- настройки комнаты

    async def toggle_lock(self, channel: discord.VoiceChannel) -> bool:
        locked = not is_locked(channel)
        await perms.update_overwrite(
            channel, channel.guild.default_role, connect=False if locked else True, reason="Triarch: замок"
        )
        return locked

    async def raise_room(self, channel: discord.VoiceChannel) -> None:
        """Поднимает комнату наверх категории — сразу под канал-триггер, если он там же."""
        retry = self._raise_cooldown.remaining(channel.id)
        if retry:
            raise RateLimited(retry, "Комнату можно поднимать не так часто")

        trigger = discord.utils.get(channel.guild.voice_channels, name=self.config.trigger_channel_name)
        if trigger and trigger.category_id == channel.category_id and trigger.id != channel.id:
            await channel.move(after=trigger, reason="Triarch: поднять комнату")
        else:
            await channel.move(beginning=True, reason="Triarch: поднять комнату")
        self._raise_cooldown.trigger(channel.id)

    async def set_limit(self, channel: discord.VoiceChannel, raw_value: str) -> int:
        value = raw_value.strip()
        if not value.isdigit() or not 0 <= int(value) <= MAX_USER_LIMIT:
            raise InvalidInput(f"Введите число от 0 до {MAX_USER_LIMIT}.")
        limit = int(value)
        await channel.edit(user_limit=limit, reason="Triarch: лимит")
        return limit

    async def rename(self, channel: discord.VoiceChannel, raw_name: str) -> str:
        name = sanitize_room_name(raw_name)
        retry = self._rename_limiter.hit(channel.id)
        if retry:
            raise RateLimited(retry, "Discord разрешает переименовывать канал 2 раза в 10 минут")
        try:
            # Без таймаута discord.py может «уснуть» на минуты, ожидая rate limit.
            await asyncio.wait_for(channel.edit(name=name, reason="Triarch: переименование"), RENAME_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise RateLimited(60, "Discord временно ограничил переименование") from exc
        return name

    # ---------------------------------------------------------- владение

    async def _grant_owner(self, channel: discord.VoiceChannel, old_id: int | None, new_owner: discord.Member) -> None:
        if old_id and old_id != new_owner.id:
            old_owner = channel.guild.get_member(old_id)
            if old_owner:
                await perms.update_overwrite(channel, old_owner, reason="Triarch: смена владельца", **perms.OWNER_RESET)
        await perms.update_overwrite(channel, new_owner, reason="Triarch: новый владелец", **perms.OWNER_PERMS)
        self.repository.set_owner_id(channel.id, new_owner.id)

    async def transfer_ownership(
        self, channel: discord.VoiceChannel, actor: discord.Member, new_owner: discord.Member
    ) -> None:
        if new_owner.bot or new_owner.id == actor.id:
            raise InvalidTarget("Выберите другого участника.")
        if new_owner not in channel.members:
            raise InvalidTarget("Передать комнату можно только тому, кто сейчас в ней.")
        self.ensure_can_use_rooms(new_owner, "владеть комнатой")
        await self._grant_owner(channel, self.repository.get_owner_id(channel.id), new_owner)

    def can_own(self, member: discord.Member) -> bool:
        return not member.bot and not self.is_user_muted(member) and not self.is_user_unverified(member)

    def transfer_candidates(self, channel: discord.VoiceChannel, actor: discord.Member) -> List[discord.Member]:
        """Кому владелец может передать комнату: только те, кто сейчас в ней."""
        return [m for m in self._members_by_join_order(channel) if m.id != actor.id and self.can_own(m)]

    def _members_by_join_order(self, channel: discord.VoiceChannel) -> List[discord.Member]:
        present = {m.id: m for m in human_members(channel)}
        ordered = [present.pop(uid) for uid in self.repository.join_order(channel.id) if uid in present]
        return ordered + list(present.values())  # кого нет в истории (например, после перезапуска) — в конец

    def pick_successor(self, channel: discord.VoiceChannel) -> discord.Member | None:
        """Следующий владелец — тот, кто раньше всех зашёл из оставшихся."""
        return next((m for m in self._members_by_join_order(channel) if self.can_own(m)), None)

    async def reassign_if_owner_absent(self, channel: discord.VoiceChannel) -> discord.Member | None:
        """Если владельца нет в комнате — передаёт её следующему. Возвращает нового владельца."""
        owner_id = self.repository.get_owner_id(channel.id)
        if owner_id is None or any(m.id == owner_id for m in channel.members):
            return None
        successor = self.pick_successor(channel)
        if successor is None:
            return None
        await self._grant_owner(channel, owner_id, successor)
        logger.info("Комната #%s перешла к %s", channel.name, successor)
        return successor

    async def reassign_absent_owners(
        self, get_channel: ChannelGetter
    ) -> List[Tuple[discord.VoiceChannel, discord.Member]]:
        """Фоновая страховка: на случай пропущенных событий (например, при перезапуске)."""
        changed = []
        for channel_id in self.repository.room_ids():
            channel = get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                new_owner = await self.reassign_if_owner_absent(channel)
                if new_owner:
                    changed.append((channel, new_owner))
        return changed

    # --------------------------------------------------------- модерация

    async def toggle_mute(self, channel: discord.VoiceChannel, actor: discord.Member, target: discord.Member) -> bool:
        """Мут, если участник не замучен, иначе размут. Возвращает новое состояние.

        Запрет `speak` в комнате — постоянная часть (действует и при перезаходе),
        серверный мут — мгновенная (Discord применяет `speak` только при новом подключении).
        """
        muted = not is_voice_muted(channel, target)
        self.validate_target(channel, actor, target, must_be_in_room=muted)
        await perms.update_overwrite(
            channel,
            target,
            reason="Triarch: мут" if muted else "Triarch: размут",
            **(perms.VOICE_MUTED if muted else perms.VOICE_UNMUTED),
        )
        await self.sync_server_mute(target)
        return muted

    # ------------------------------------------------------ серверный мут

    def _should_be_server_muted(self, member: discord.Member) -> bool:
        channel = member.voice.channel if member.voice else None
        return (
            isinstance(channel, discord.VoiceChannel)
            and self.repository.is_temp_room(channel.id)
            and is_voice_muted(channel, member)
        )

    async def sync_server_mute(self, member: discord.Member) -> None:
        """Приводит серверный мут в соответствие с тем, где сейчас участник.

        - В комнате, где он замучен, — включает серверный мут и запоминает это.
        - В любом другом канале — снимает мут, но только если его выдал бот.
        - Вне голоса Discord не даёт менять мут: снимем при следующем подключении.
        """
        voice = member.voice
        if voice is None or voice.channel is None:
            return
        guild_id = member.guild.id
        tracked = self.repository.is_server_muted(guild_id, member.id)

        if self._should_be_server_muted(member):
            if not voice.mute:
                await member.edit(mute=True, reason="Triarch: мут в комнате")
                self.repository.add_server_muted(guild_id, member.id)
        elif tracked:
            if voice.mute:
                await member.edit(mute=False, reason="Triarch: снятие мута комнаты")
            self.repository.remove_server_muted(guild_id, member.id)

    async def toggle_ban(self, channel: discord.VoiceChannel, actor: discord.Member, target: discord.Member) -> bool:
        """Бан, если участник не забанен, иначе разбан. Возвращает новое состояние."""
        if self.repository.is_banned(channel.id, target.id):
            await perms.update_overwrite(channel, target, reason="Triarch: разбан", **perms.ACCESS_RESET)
            self.repository.unban_user(channel.id, target.id)
            return False

        self.validate_target(channel, actor, target, must_be_in_room=False)
        if self.repository.get_owner_id(channel.id) == target.id:
            raise InvalidTarget("Нельзя забанить владельца комнаты.")
        await perms.update_overwrite(channel, target, reason="Triarch: бан", **perms.NO_ACCESS)
        self.repository.ban_user(channel.id, target.id)
        if target in channel.members:
            await target.move_to(None, reason="Triarch: бан в комнате")
        return True
