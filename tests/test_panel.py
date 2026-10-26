from unittest.mock import AsyncMock, MagicMock

import discord

from cogs.triarch_voice.repository import TriarchRepository
from cogs.triarch_voice.service import TriarchService
from cogs.triarch_voice.ui import ControlPanelView
from cogs.triarch_voice.ui.layout import ALL_BUTTONS, PANEL_GRID
from tests.factories import make_config, make_guild, make_member, make_voice_channel


async def test_panel_is_persistent_grid():
    view = ControlPanelView(TriarchService(TriarchRepository(), make_config()), TriarchRepository())
    assert view.is_persistent()
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    assert len(buttons) == len(ALL_BUTTONS) == 7

    rows = {}
    for b in buttons:
        rows.setdefault(b.row, []).append(b)
    assert [len(rows[r]) for r in sorted(rows)] == [len(r) for r in PANEL_GRID] == [4, 3]
    assert len({b.custom_id for b in buttons}) == 7


def _interaction(user, channel):
    inter = MagicMock(spec=discord.Interaction)
    inter.user = user
    inter.channel = channel
    inter.response = MagicMock()
    inter.response.is_done = MagicMock(return_value=False)
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()
    return inter


def _button(view, key):
    return next(b for b in view.children if b.custom_id.endswith(":" + key))


async def test_non_owner_cannot_press_owner_buttons():
    repo = TriarchRepository()
    view = ControlPanelView(TriarchService(repo, make_config()), repo)
    guild = make_guild()
    owner, stranger = make_member(1, guild), make_member(2, guild)
    channel = make_voice_channel(50, guild, members=[owner, stranger])
    repo.register_room(50, guild.id, owner.id)

    inter = _interaction(stranger, channel)
    await _button(view, "lock").callback(inter)
    text = inter.response.send_message.await_args.args[0]
    assert "Только владелец" in text
    channel.set_permissions.assert_not_awaited()


async def test_owner_lock_and_spam_protection():
    repo = TriarchRepository()
    view = ControlPanelView(TriarchService(repo, make_config(panel_actions=2)), repo)
    guild = make_guild()
    owner = make_member(1, guild)
    channel = make_voice_channel(50, guild, members=[owner])
    channel.send = AsyncMock()
    repo.register_room(50, guild.id, owner.id)

    for _ in range(2):
        await _button(view, "lock").callback(_interaction(owner, channel))
    assert channel.set_permissions.await_count == 2

    spam = _interaction(owner, channel)
    await _button(view, "lock").callback(spam)
    assert "Слишком много нажатий" in spam.response.send_message.await_args.args[0]
    assert channel.set_permissions.await_count == 2


async def test_panel_outside_room_is_rejected():
    repo = TriarchRepository()
    view = ControlPanelView(TriarchService(repo, make_config()), repo)
    guild = make_guild()
    inter = _interaction(make_member(1, guild), make_voice_channel(99, guild))
    await _button(view, "lock").callback(inter)
    assert "больше не управляется" in inter.response.send_message.await_args.args[0]


async def test_legend_is_one_button_per_line():
    from cogs.triarch_voice.ui.embeds import _legend

    lines = _legend().splitlines()
    assert len(lines) == len(ALL_BUTTONS)
    assert all(" — " in line for line in lines)
