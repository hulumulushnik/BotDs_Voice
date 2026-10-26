"""Слой событий Discord: принимает события и делегирует логику в TriarchService."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from config import BotConfig
from core.audit_log import AuditLog

from .errors import TriarchError
from .repository import TriarchRepository
from .service import TriarchService
from .ui import ControlPanelView, refresh_panel, send_panel
from .ui.views import announce

logger = logging.getLogger(__name__)


class TriarchVoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: BotConfig, audit: AuditLog) -> None:
        self.bot = bot
        self.config = config
        self.audit = audit
        self.repository = TriarchRepository(config.data_file)
        self.service = TriarchService(self.repository, config)
        self.panel_view = ControlPanelView(self.service, self.repository)
        self._startup_logged = False
        self.cleanup_loop.change_interval(seconds=config.cleanup_interval)

    # ------------------------------------------------------------ lifecycle

    async def cog_load(self) -> None:
        # Persistent view: кнопки старых панелей работают и после перезапуска.
        self.bot.add_view(self.panel_view)
        self.cleanup_loop.start()

    async def cog_unload(self) -> None:
        self.cleanup_loop.cancel()

    @tasks.loop(seconds=30)
    async def cleanup_loop(self) -> None:
        for channel in await self.service.cleanup(self.bot.get_channel):
            await self._log_room_deleted(channel, "фоновая проверка")
        for channel, new_owner in await self.service.reassign_absent_owners(self.bot.get_channel):
            await self._on_owner_changed(channel, new_owner)
        for guild in self.bot.guilds:  # страховка, если событие было пропущено
            for user_id in self.repository.server_muted_in(guild.id):
                member = guild.get_member(user_id)
                if member:
                    await self._sync_server_mute(member)

    @cleanup_loop.before_loop
    async def _before_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    @cleanup_loop.error
    async def _cleanup_error(self, error: BaseException) -> None:
        logger.exception("Ошибка фоновой уборки комнат", exc_info=error)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # on_ready вызывается и при каждом переподключении — логируем старт один раз.
        if self._startup_logged or not self.bot.user:
            return
        self._startup_logged = True
        await self._refresh_all_panels()
        user = self.bot.user
        logger.info("Бот запущен: %s (ID: %s)", user, user.id)

        fields = [
            ("ID бота", str(user.id)),
            ("Тег", str(user)),
            ("Время запуска", discord.utils.format_dt(discord.utils.utcnow(), "F")),
            ("Активных комнат", str(len(self.repository.room_ids()))),
        ]
        try:
            app = await self.bot.application_info()
            fields.append(("Создатель", f"{app.owner} (ID: {app.owner.id})"))
        except discord.HTTPException as exc:
            logger.warning("Не удалось получить информацию о приложении: %s", exc)

        await self.audit.broadcast(
            self.bot.guilds,
            "Бот запущен",
            "Triarch Voice Bot успешно инициализирован.",
            discord.Color.green(),
            fields,
        )

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        # Только в консоль: при обрыве сети отправка в Discord всё равно не пройдёт,
        # а частые переподключения заспамили бы лог-канал.
        logger.warning("Соединение с Discord Gateway разорвано")

    # --------------------------------------------------------------- events

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot or before.channel == after.channel:
            return
        # Сначала выход, потом вход: если владелец идёт из своей комнаты в триггер,
        # старая комната успеет перейти к следующему участнику до создания новой.
        if before.channel and self.repository.is_temp_room(before.channel.id):
            await self._handle_leave(member, before.channel)
        if after.channel:
            await self._handle_join(member, after.channel)
            await self._sync_server_mute(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.roles == after.roles or not self.service.is_user_muted(after):
            return
        channel = after.voice.channel if after.voice else None
        if channel and self.repository.is_temp_room(channel.id):
            await self._deny(after, "Заглушённые пользователи не могут находиться в комнатах Triarch.")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if self.repository.is_temp_room(channel.id):
            self.service.forget_room(channel.id)
            logger.info("Комната #%s удалена вручную", channel.name)

    # -------------------------------------------------------------- helpers

    async def _handle_join(self, member: discord.Member, channel: discord.abc.Connectable) -> None:
        if not isinstance(channel, discord.VoiceChannel):
            return
        if self.repository.is_temp_room(channel.id):
            if self.service.is_user_muted(member):
                await self._deny(member, "Заглушённые пользователи не могут находиться в комнатах Triarch.")
            else:
                self.repository.mark_joined(channel.id, member.id)
            return
        if channel.name == self.config.trigger_channel_name:
            await self._create_room(member, channel)

    async def _sync_server_mute(self, member: discord.Member) -> None:
        try:
            await self.service.sync_server_mute(member)
        except discord.HTTPException as exc:
            logger.warning("Не удалось обновить серверный мут %s: %s", member, exc)

    async def _handle_leave(self, member: discord.Member, channel: discord.VoiceChannel) -> None:
        self.repository.mark_left(channel.id, member.id)
        if await self.service.delete_if_empty(channel):
            await self._log_room_deleted(channel, "все вышли")
            return
        new_owner = await self.service.reassign_if_owner_absent(channel)
        if new_owner:
            await self._on_owner_changed(channel, new_owner)

    async def _on_owner_changed(self, channel: discord.VoiceChannel, new_owner: discord.Member) -> None:
        await refresh_panel(channel, self.repository)
        await announce(channel, f"👑 Владелец вышел — комната переходит к {new_owner.mention}.")
        await self.audit.send(
            channel.guild,
            "Смена владельца",
            f"Комната #{channel.name} перешла к {new_owner.mention}.",
            discord.Color.gold(),
        )

    async def _refresh_all_panels(self) -> None:
        """После обновления бота перерисовывает старые панели под текущую раскладку кнопок."""
        for channel_id in self.repository.room_ids():
            channel = self.bot.get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                await refresh_panel(channel, self.repository, view=self.panel_view)

    async def _create_room(self, member: discord.Member, trigger: discord.VoiceChannel) -> None:
        try:
            room = await self.service.create_room(member, trigger)
        except TriarchError as exc:
            await self._deny(member, str(exc))
            return
        except discord.HTTPException as exc:
            logger.error("Не удалось создать комнату для %s: %s", member, exc)
            await self._deny(member, "Не удалось создать комнату. Попробуйте позже.")
            return

        if room is None:
            return
        try:
            await member.move_to(room, reason="Triarch: перенос в свою комнату")
        except discord.HTTPException:
            # Пользователь уже вышел из триггер-канала — пустую комнату сразу убираем.
            if await self.service.delete_if_empty(room, grace=0):
                await self._log_room_deleted(room, "владелец не дождался создания")
            return

        await send_panel(room, member, self.panel_view, self.repository)
        await self.audit.send(
            member.guild,
            "Создана комната Triarch",
            f"Владелец: {member.mention}",
            discord.Color.green(),
            [("Канал", f"#{room.name}"), ("ID", str(room.id))],
        )

    async def _deny(self, member: discord.Member, reason: str) -> None:
        channel = member.voice.channel if member.voice else None
        logger.info("Отказ в доступе %s: %s", member, reason)
        try:
            await member.move_to(None, reason=f"Triarch: {reason}")
        except discord.HTTPException as exc:
            logger.error("Не удалось отключить %s: %s", member, exc)
        try:
            await member.send(f"⛔ {reason}")
        except discord.HTTPException:
            pass  # закрытые ЛС — не ошибка

        roles = [role.name for role in member.roles if not role.is_default()]
        await self.audit.send(
            member.guild,
            "Отказ в доступе",
            f"{member.mention} отключён от голосового канала.",
            discord.Color.orange(),
            [
                ("Причина", reason),
                ("Роли", ", ".join(roles) or "Нет"),
                ("Канал", f"#{channel.name}" if channel else "Неизвестно"),
            ],
        )

    async def _log_room_deleted(self, channel: discord.VoiceChannel, why: str) -> None:
        await self.audit.send(
            channel.guild,
            "Удалена комната Triarch",
            f"Канал #{channel.name} удалён ({why}).",
            discord.Color.red(),
        )
