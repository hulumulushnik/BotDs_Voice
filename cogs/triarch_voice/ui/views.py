"""Интерактивные компоненты: панель управления, модальные окна, выбор участника.

Панель — persistent view: у всех кнопок постоянный custom_id, а комната
определяется по interaction.channel. Поэтому один экземпляр view, зарегистрированный
через bot.add_view(), обслуживает все комнаты и продолжает работать после перезапуска.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from typing import Awaitable, Callable, Dict, List, Tuple

import discord

from ..errors import TriarchError
from ..repository import TriarchRepository
from ..service import TriarchService
from .embeds import build_panel_embed
from .layout import ALL_BUTTONS, PANEL_GRID, PanelButton

logger = logging.getLogger(__name__)

# (interaction, комната, участник-инициатор) -> None
RoomAction = Callable[[discord.Interaction, discord.VoiceChannel, discord.Member], Awaitable[None]]

_NO_MENTIONS = discord.AllowedMentions.none()
_USER_MENTIONS = discord.AllowedMentions(users=True, everyone=False, roles=False)


# --------------------------------------------------------------------- helpers


async def reply(interaction: discord.Interaction, text: str | None = None, **kwargs) -> None:
    """Эфемерный ответ, независимо от того, был ли уже ответ на interaction."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(text, ephemeral=True, **kwargs)


async def announce(channel: discord.VoiceChannel, text: str) -> None:
    """Публичное сообщение в чат комнаты (ошибки отправки не критичны)."""
    try:
        await channel.send(text, allowed_mentions=_NO_MENTIONS)
    except discord.HTTPException as exc:
        logger.debug("Не удалось написать в #%s: %s", channel.name, exc)


async def refresh_panel(
    channel: discord.VoiceChannel,
    repository: TriarchRepository,
    view: discord.ui.View | None = None,
) -> None:
    """Обновляет embed панели. С `view` — ещё и кнопки (нужно после смены раскладки)."""
    room = repository.get(channel.id)
    if not room or not room.panel_message_id:
        return
    extra = {"view": view} if view is not None else {}
    try:
        await channel.get_partial_message(room.panel_message_id).edit(
            embed=build_panel_embed(channel, room.owner_id), **extra
        )
    except discord.HTTPException as exc:
        logger.debug("Не удалось обновить панель в #%s: %s", channel.name, exc)


class ActionRunner:
    """Единая точка входа для любых действий с комнатой.

    Проверяет комнату, антиспам и права, ловит ошибки и отвечает пользователю.
    """

    def __init__(self, service: TriarchService, repository: TriarchRepository) -> None:
        self.service = service
        self.repository = repository

    async def run(self, interaction: discord.Interaction, action: RoomAction) -> None:
        member = interaction.user
        try:
            if not isinstance(member, discord.Member):
                raise TriarchError("Действие доступно только на сервере.")
            channel = self.service.require_room(interaction.channel)
            self.service.check_panel_rate(member.id)
            self.service.require_manager(channel, member)
            await action(interaction, channel, member)
        except TriarchError as exc:
            await reply(interaction, str(exc))
        except discord.Forbidden:
            logger.warning("Недостаточно прав у бота для действия в %s", interaction.channel)
            await reply(interaction, "У бота недостаточно прав для этого действия. Сообщите администрации.")
        except discord.HTTPException as exc:
            logger.error("Ошибка Discord API: %s", exc)
            await reply(interaction, "Discord вернул ошибку. Попробуйте ещё раз чуть позже.")


# ----------------------------------------------------------------------- modals


class TextPromptModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        label: str,
        placeholder: str,
        max_length: int,
        on_value: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title, timeout=300)
        self._on_value = on_value
        self.field = discord.ui.TextInput(label=label, placeholder=placeholder, min_length=1, max_length=max_length)
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_value(interaction, self.field.value)


# --------------------------------------------------------------- member select

# Результат действия: (ответ инициатору, сообщение в чат комнаты); {target} подставляется.
Outcome = Tuple[str, str]


@dataclass(frozen=True)
class MemberAction:
    prompt: str
    run: Callable[[discord.VoiceChannel, discord.Member, discord.Member], Awaitable[Outcome]]
    refresh: bool = False


class MemberSelectView(discord.ui.View):
    """Эфемерный выбор участника для одного действия.

    Без `options` — выбор любого участника сервера, с `options` — только из списка.
    """

    def __init__(self, runner: ActionRunner, action: MemberAction, options: List[discord.Member] | None = None):
        super().__init__(timeout=60)
        self.runner = runner
        self.action = action

        if options is None:
            select: discord.ui.Select = discord.ui.UserSelect(placeholder="Выберите участника…")
        else:
            select = discord.ui.Select(
                placeholder="Выберите участника…",
                options=[discord.SelectOption(label=m.display_name[:100], value=str(m.id)) for m in options[:25]],
            )
        select.callback = partial(self._on_select, select)
        self.add_item(select)

    async def _on_select(self, select: discord.ui.Select, interaction: discord.Interaction) -> None:
        async def execute(inter: discord.Interaction, channel: discord.VoiceChannel, actor: discord.Member) -> None:
            target = self._resolve_target(select, channel)
            if target is None:
                raise TriarchError("Участник не найден на сервере.")
            await inter.response.defer(ephemeral=True)
            private, public = await self.action.run(channel, actor, target)
            if self.action.refresh:
                await refresh_panel(channel, self.runner.repository)
            await reply(inter, private.format(target=target.mention))
            await announce(channel, public.format(target=target.mention))
            self.stop()

        await self.runner.run(interaction, execute)

    @staticmethod
    def _resolve_target(select: discord.ui.Select, channel: discord.VoiceChannel) -> discord.Member | None:
        value = select.values[0]
        if isinstance(value, discord.Member):
            return value
        if isinstance(value, (discord.User, discord.Object)):
            return channel.guild.get_member(value.id)
        return channel.guild.get_member(int(value))


# --------------------------------------------------------------- control panel


class ControlPanelView(discord.ui.View):
    """Панель управления комнатой: сетка кнопок из layout.PANEL_GRID."""

    def __init__(self, service: TriarchService, repository: TriarchRepository) -> None:
        super().__init__(timeout=None)
        self.service = service
        self.repository = repository
        self.runner = ActionRunner(service, repository)

        handlers: Dict[str, RoomAction] = {
            "rename": self._rename,
            "lock": self._lock,
            "limit": self._limit,
            "raise": self._raise,
            "mute": self._select(MemberAction("Кого замутить или размутить?", self._toggle_mute)),
            "ban": self._select(MemberAction("Кого забанить или разбанить?", self._toggle_ban)),
            "transfer": self._transfer,
        }
        missing = {b.key for b in ALL_BUTTONS} - handlers.keys()
        if missing:
            raise RuntimeError(f"Нет обработчиков для кнопок: {missing}")

        for row_index, row in enumerate(PANEL_GRID):
            for spec in row:
                self.add_item(self._make_button(spec, row_index, handlers[spec.key]))

    def _make_button(self, spec: PanelButton, row: int, handler: RoomAction) -> discord.ui.Button:
        button = discord.ui.Button(emoji=spec.emoji, style=spec.style, custom_id=spec.custom_id, row=row)

        async def callback(interaction: discord.Interaction) -> None:
            await self.runner.run(interaction, handler)

        button.callback = callback
        return button

    # ---------- действия с выбором участника

    def _select(self, action: MemberAction) -> RoomAction:
        async def open_select(interaction: discord.Interaction, _ch: discord.VoiceChannel, _m: discord.Member) -> None:
            await reply(interaction, action.prompt, view=MemberSelectView(self.runner, action))

        return open_select

    async def _toggle_mute(
        self, channel: discord.VoiceChannel, actor: discord.Member, target: discord.Member
    ) -> Outcome:
        if await self.service.toggle_mute(channel, actor, target):
            return "🔇 {target} замучен.", "🔇 {target} замучен в этой комнате."
        return "🔊 {target} размучен.", "🔊 {target} снова может говорить."

    async def _toggle_ban(
        self, channel: discord.VoiceChannel, actor: discord.Member, target: discord.Member
    ) -> Outcome:
        if await self.service.toggle_ban(channel, actor, target):
            return "🔨 {target} забанен.", "🔨 {target} забанен в этой комнате."
        return "✅ {target} разбанен.", "✅ {target} разбанен в этой комнате."

    async def _transfer(
        self, interaction: discord.Interaction, channel: discord.VoiceChannel, member: discord.Member
    ) -> None:
        # Список только из тех, кто сейчас в комнате.
        candidates = self.service.transfer_candidates(channel, member)
        if not candidates:
            raise TriarchError("В комнате нет участников, которым можно передать владение.")

        async def run(ch: discord.VoiceChannel, actor: discord.Member, target: discord.Member) -> Outcome:
            await self.service.transfer_ownership(ch, actor, target)
            return "👑 Права переданы {target}.", "👑 {target} теперь владелец комнаты!"

        action = MemberAction("Кому передать комнату?", run, refresh=True)
        await reply(interaction, action.prompt, view=MemberSelectView(self.runner, action, options=candidates))

    # ---------- настройки

    async def _rename(self, interaction: discord.Interaction, _ch: discord.VoiceChannel, _m: discord.Member) -> None:
        async def apply(inter: discord.Interaction, value: str) -> None:
            async def execute(i: discord.Interaction, channel: discord.VoiceChannel, _a: discord.Member) -> None:
                await i.response.defer(ephemeral=True)
                name = await self.service.rename(channel, value)
                await reply(i, f"✏️ Название изменено на **{discord.utils.escape_markdown(name)}**.")

            await self.runner.run(inter, execute)

        await interaction.response.send_modal(
            TextPromptModal(
                title="Переименование",
                label="Новое название",
                placeholder="Например: Играем в CS2",
                max_length=90,
                on_value=apply,
            )
        )

    async def _limit(self, interaction: discord.Interaction, _ch: discord.VoiceChannel, _m: discord.Member) -> None:
        async def apply(inter: discord.Interaction, value: str) -> None:
            async def execute(i: discord.Interaction, channel: discord.VoiceChannel, _a: discord.Member) -> None:
                limit = await self.service.set_limit(channel, value)
                await refresh_panel(channel, self.repository)
                await reply(i, "👥 Лимит снят." if limit == 0 else f"👥 Лимит: {limit}.")

            await self.runner.run(inter, execute)

        await interaction.response.send_modal(
            TextPromptModal(
                title="Лимит участников",
                label="Лимит (0–99)",
                placeholder="0 — без лимита",
                max_length=2,
                on_value=apply,
            )
        )

    async def _raise(self, interaction: discord.Interaction, channel: discord.VoiceChannel, _m: discord.Member) -> None:
        await self.service.raise_room(channel)
        await reply(interaction, "⬆️ Комната поднята наверх.")

    async def _lock(self, interaction: discord.Interaction, channel: discord.VoiceChannel, _m: discord.Member) -> None:
        locked = await self.service.toggle_lock(channel)
        await refresh_panel(channel, self.repository)
        text = "🔒 Комната закрыта для новых участников." if locked else "🔓 Комната открыта для всех."
        await reply(interaction, text)
        await announce(channel, text)


async def send_panel(
    channel: discord.VoiceChannel,
    owner: discord.Member,
    view: ControlPanelView,
    repository: TriarchRepository,
) -> None:
    """Публикует панель в чате комнаты и запоминает id сообщения."""
    message = await channel.send(
        content=f"🏗 {owner.mention}, ваша комната готова!",
        embed=build_panel_embed(channel, owner.id),
        view=view,
        allowed_mentions=_USER_MENTIONS,
    )
    repository.set_panel_message(channel.id, message.id)
