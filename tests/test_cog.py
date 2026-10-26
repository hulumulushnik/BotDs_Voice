from unittest.mock import AsyncMock, MagicMock

import discord

from cogs.triarch_voice.cog import TriarchVoiceCog
from tests.factories import make_config, make_guild, make_member, make_voice_channel


def _cog():
    audit = MagicMock()
    audit.send = AsyncMock()
    cog = TriarchVoiceCog(MagicMock(), make_config(), audit)
    cog.service.delete_if_empty = AsyncMock(return_value=False)
    return cog


def _state(channel):
    return MagicMock(spec=discord.VoiceState, channel=channel)


async def test_owner_leaving_passes_room_to_next_member():
    cog = _cog()
    guild = make_guild()
    owner, guest = make_member(1, guild), make_member(2, guild)
    room = make_voice_channel(50, guild)
    room.send = AsyncMock()
    cog.repository.register_room(50, guild.id, owner.id)

    room.members = [owner]
    await cog.on_voice_state_update(owner, _state(None), _state(room))
    room.members = [owner, guest]
    await cog.on_voice_state_update(guest, _state(None), _state(room))
    assert cog.repository.join_order(50) == [1, 2]

    room.members = [guest]
    await cog.on_voice_state_update(owner, _state(room), _state(None))
    assert cog.repository.get_owner_id(50) == guest.id
    assert "переходит" in room.send.await_args.args[0]
    cog.audit.send.assert_awaited()


async def test_owner_creates_new_room_from_his_room():
    """Владелец из своей комнаты заходит в триггер: старая уходит гостю, создаётся новая."""
    cog = _cog()
    guild = make_guild()
    owner, guest = make_member(1, guild), make_member(2, guild)
    old_room = make_voice_channel(50, guild)
    old_room.send = AsyncMock()
    cog.repository.register_room(50, guild.id, owner.id)
    for m in (owner, guest):
        cog.repository.mark_joined(50, m.id)

    trigger = make_voice_channel(1, guild)
    trigger.name = cog.config.trigger_channel_name
    new_room = make_voice_channel(60, guild)
    new_room.send = AsyncMock(return_value=MagicMock(id=999))
    guild.create_voice_channel = AsyncMock(return_value=new_room)

    old_room.members = [guest]
    await cog.on_voice_state_update(owner, _state(old_room), _state(trigger))

    assert cog.repository.get_owner_id(50) == guest.id  # старая комната — гостю
    assert cog.repository.get_owner_id(60) == owner.id  # новая — владельцу
    owner.move_to.assert_awaited_with(new_room, reason="Triarch: перенос в свою комнату")
