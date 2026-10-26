import json

from cogs.triarch_voice.repository import TriarchRepository


def test_state_survives_restart(tmp_path):
    path = tmp_path / "rooms.json"
    repo = TriarchRepository(path)
    repo.register_room(10, guild_id=1, owner_id=100)
    repo.ban_user(10, 200)
    repo.set_panel_message(10, 555)

    restored = TriarchRepository(path)
    room = restored.get(10)
    assert room.owner_id == 100
    assert room.banned_ids == {200}
    assert room.panel_message_id == 555
    assert restored.find_by_owner(1, 100).channel_id == 10


def test_remove_room_persists(tmp_path):
    path = tmp_path / "rooms.json"
    repo = TriarchRepository(path)
    repo.register_room(10, 1, 100)
    repo.remove_room(10)
    assert json.loads(path.read_text())["rooms"] == []


def test_corrupted_file_does_not_crash(tmp_path):
    path = tmp_path / "rooms.json"
    path.write_text("{not json")
    assert TriarchRepository(path).room_ids() == []


def test_in_memory_mode():
    repo = TriarchRepository()
    repo.register_room(1, 1, 1)
    assert repo.is_temp_room(1)


def test_join_order_persists(tmp_path):
    path = tmp_path / "rooms.json"
    repo = TriarchRepository(path)
    repo.register_room(10, 1, 100)
    for uid in (100, 200, 300, 200):
        repo.mark_joined(10, uid)
    repo.mark_left(10, 100)
    assert TriarchRepository(path).join_order(10) == [200, 300]


def test_server_muted_persists(tmp_path):
    path = tmp_path / "rooms.json"
    repo = TriarchRepository(path)
    repo.add_server_muted(1, 200)
    restored = TriarchRepository(path)
    assert restored.is_server_muted(1, 200)
    assert restored.server_muted_in(1) == [200]
    restored.remove_server_muted(1, 200)
    assert not TriarchRepository(path).is_server_muted(1, 200)
