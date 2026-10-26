from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.triarch_voice.errors import AccessDenied, InvalidInput, InvalidTarget, RateLimited
from cogs.triarch_voice.repository import TriarchRepository
from cogs.triarch_voice.service import TriarchService, sanitize_room_name
from tests.factories import make_config, make_guild, make_member, make_role, make_voice_channel


@pytest.fixture
def setup():
    guild = make_guild()
    repo = TriarchRepository()
    service = TriarchService(repo, make_config())
    trigger = make_voice_channel(1, guild)
    counter = iter(range(100, 200))
    guild.create_voice_channel = _create_channel_factory(guild, counter)
    return guild, repo, service, trigger


def _create_channel_factory(guild, counter):
    async def create_voice_channel(**kwargs):
        channel = make_voice_channel(next(counter), guild)
        guild.get_channel.side_effect = lambda cid, ch=channel: ch if cid == ch.id else None
        return channel

    return create_voice_channel


# ---------- имя комнаты


@pytest.mark.parametrize("raw, expected", [("  Играем   в  CS2 ", "Играем в CS2"), ("x" * 150, "x" * 100)])
def test_sanitize_room_name(raw, expected):
    assert sanitize_room_name(raw) == expected


@pytest.mark.parametrize("raw", ["   ", "заходи discord.gg/abc", "https://evil.example"])
def test_sanitize_rejects(raw):
    with pytest.raises(InvalidInput):
        sanitize_room_name(raw)


# ---------- создание комнат и антиспам


async def test_create_cooldown(setup):
    guild, repo, service, trigger = setup
    member = make_member(5, guild)
    assert await service.create_room(member, trigger) is not None
    with pytest.raises(RateLimited):
        await service.create_room(member, trigger)


async def test_guild_room_limit(setup):
    guild, repo, service, trigger = setup
    for uid in (1, 2):
        await service.create_room(make_member(uid, guild), trigger)
    with pytest.raises(AccessDenied):
        await service.create_room(make_member(3, guild), trigger)


async def test_muted_and_unverified_cannot_create():
    muted, unverified = make_role("Заглушён"), make_role("Не верифицирован")
    guild = make_guild(roles=[muted, unverified])
    service = TriarchService(TriarchRepository(), make_config())
    trigger = make_voice_channel(1, guild)
    for role in (muted, unverified):
        with pytest.raises(AccessDenied):
            await service.create_room(make_member(5, guild, roles=[role]), trigger)


def test_panel_rate_limit(setup):
    _, _, service, _ = setup
    for _ in range(3):
        service.check_panel_rate(1)
    with pytest.raises(RateLimited):
        service.check_panel_rate(1)


# ---------- модерация


async def test_cannot_target_moderator_self_or_bot(setup):
    guild, repo, service, _ = setup
    owner = make_member(1, guild)
    channel = make_voice_channel(50, guild)
    repo.register_room(50, guild.id, owner.id)
    for target in (owner, make_member(2, guild, bot=True), make_member(3, guild, moderator=True)):
        channel.members = [owner, target]
        with pytest.raises(InvalidTarget):
            await service.toggle_mute(channel, owner, target)
        with pytest.raises(InvalidTarget):
            await service.toggle_ban(channel, owner, target)


async def test_toggle_mute(setup):
    guild, repo, service, _ = setup
    owner, target = make_member(1, guild), make_member(2, guild)
    channel = make_voice_channel(50, guild, members=[owner])
    repo.register_room(50, guild.id, owner.id)

    with pytest.raises(InvalidTarget):  # мут — только тех, кто в комнате
        await service.toggle_mute(channel, owner, target)
    channel.members = [owner, target]
    assert await service.toggle_mute(channel, owner, target) is True
    channel.overwrites_for.return_value = discord.PermissionOverwrite(speak=False)
    assert await service.toggle_mute(channel, owner, target) is False


def _in_voice(member, channel, *, server_muted=False):
    member.voice = MagicMock(channel=channel, mute=server_muted)
    member.edit = AsyncMock(side_effect=lambda **kw: setattr(member.voice, "mute", kw["mute"]))


async def test_mute_is_instant_via_server_mute(setup):
    guild, repo, service, _ = setup
    owner, target = make_member(1, guild), make_member(2, guild)
    channel = make_voice_channel(50, guild, members=[owner, target])
    repo.register_room(50, guild.id, owner.id)
    _in_voice(target, channel)

    overwrites = {}

    async def set_permissions(member, overwrite=None, **kw):
        overwrites[member.id] = overwrite or discord.PermissionOverwrite()

    channel.set_permissions.side_effect = set_permissions
    channel.overwrites_for.side_effect = lambda m: overwrites.get(m.id, discord.PermissionOverwrite())

    assert await service.toggle_mute(channel, owner, target) is True
    target.edit.assert_awaited_with(mute=True, reason="Triarch: мут в комнате")
    target.move_to.assert_not_awaited()  # никакого переподключения
    assert repo.is_server_muted(guild.id, target.id)

    assert await service.toggle_mute(channel, owner, target) is False
    target.edit.assert_awaited_with(mute=False, reason="Triarch: снятие мута комнаты")
    assert not repo.is_server_muted(guild.id, target.id)


async def test_server_mute_removed_when_leaving_room(setup):
    guild, repo, service, _ = setup
    target = make_member(2, guild)
    room = make_voice_channel(50, guild, members=[target])
    room.overwrites_for.return_value = discord.PermissionOverwrite(speak=False)
    repo.register_room(50, guild.id, 1)
    _in_voice(target, room)

    await service.sync_server_mute(target)
    assert target.voice.mute and repo.is_server_muted(guild.id, target.id)

    target.voice.channel = make_voice_channel(70, guild)  # ушёл в обычный канал
    await service.sync_server_mute(target)
    assert not target.voice.mute
    assert not repo.is_server_muted(guild.id, target.id)


async def test_foreign_server_mute_is_not_touched(setup):
    guild, repo, service, _ = setup
    target = make_member(2, guild)
    _in_voice(target, make_voice_channel(70, guild), server_muted=True)  # замучен модератором
    await service.sync_server_mute(target)
    target.edit.assert_not_awaited()


async def test_offline_member_is_left_for_later(setup):
    guild, repo, service, _ = setup
    target = make_member(2, guild)
    repo.add_server_muted(guild.id, target.id)
    target.voice = None
    target.edit = AsyncMock()
    await service.sync_server_mute(target)
    target.edit.assert_not_awaited()
    assert repo.is_server_muted(guild.id, target.id)


async def test_raise_room_has_cooldown(setup):
    guild, repo, service, trigger = setup
    trigger.name = service.config.trigger_channel_name
    trigger.category_id = 7
    guild.voice_channels = [trigger]
    channel = make_voice_channel(50, guild)
    channel.category_id = 7
    channel.move = AsyncMock()

    await service.raise_room(channel)
    assert channel.move.await_args.kwargs["after"] is trigger  # сразу под триггер
    with pytest.raises(RateLimited):
        await service.raise_room(channel)


async def test_toggle_ban(setup):
    guild, repo, service, _ = setup
    owner, target = make_member(1, guild), make_member(2, guild)
    channel = make_voice_channel(50, guild, members=[owner, target])
    repo.register_room(50, guild.id, owner.id)

    assert await service.toggle_ban(channel, owner, target) is True
    assert repo.is_banned(50, target.id)
    target.move_to.assert_awaited_once()
    assert await service.toggle_ban(channel, owner, target) is False
    assert not repo.is_banned(50, target.id)


async def test_transfer_only_to_room_members(setup):
    guild, repo, service, _ = setup
    owner, inside, outside = make_member(1, guild), make_member(2, guild), make_member(3, guild)
    channel = make_voice_channel(50, guild, members=[owner, inside, make_member(9, guild, bot=True)])
    repo.register_room(50, guild.id, owner.id)
    guild.get_member.side_effect = lambda uid: {1: owner, 2: inside, 3: outside}.get(uid)

    assert service.transfer_candidates(channel, owner) == [inside]
    with pytest.raises(InvalidTarget):
        await service.transfer_ownership(channel, owner, outside)
    await service.transfer_ownership(channel, owner, inside)
    assert repo.get_owner_id(50) == inside.id


async def test_owner_leaves_room_goes_to_earliest_joined(setup):
    muted = make_role("Заглушён")
    guild, repo, service, _ = setup
    guild.roles = [muted]
    owner = make_member(1, guild)
    first_muted = make_member(2, guild, roles=[muted])  # пропускается
    second, third = make_member(3, guild), make_member(4, guild)
    channel = make_voice_channel(50, guild)
    repo.register_room(50, guild.id, owner.id)
    for m in (owner, first_muted, second, third):
        repo.mark_joined(50, m.id)
    guild.get_member.side_effect = lambda uid: {1: owner}.get(uid)

    channel.members = [owner, third, second, first_muted]
    assert await service.reassign_if_owner_absent(channel) is None  # владелец на месте

    repo.mark_left(50, owner.id)
    channel.members = [third, second, first_muted]  # порядок в Discord не важен
    assert await service.reassign_if_owner_absent(channel) is second
    assert repo.get_owner_id(50) == second.id


async def test_rename_rate_limited(setup):
    guild, repo, service, _ = setup
    channel = make_voice_channel(50, guild)
    await service.rename(channel, "a")
    await service.rename(channel, "b")
    with pytest.raises(RateLimited):
        await service.rename(channel, "c")


async def test_set_limit_validation(setup):
    guild, _, service, _ = setup
    channel = make_voice_channel(50, guild)
    for bad in ("abc", "-1", "100"):
        with pytest.raises(InvalidInput):
            await service.set_limit(channel, bad)
    assert await service.set_limit(channel, "5") == 5


async def test_cleanup_forgets_missing_and_deletes_empty(setup):
    guild, repo, service, _ = setup
    empty = make_voice_channel(60, guild)
    busy = make_voice_channel(61, guild, members=[make_member(1, guild)])
    for ch in (empty, busy):
        repo.register_room(ch.id, guild.id, 1)
    repo.register_room(62, guild.id, 2)  # канала больше нет
    channels = {60: empty, 61: busy}

    deleted = await service.cleanup(channels.get)
    assert deleted == [empty]
    assert repo.room_ids() == [61]
