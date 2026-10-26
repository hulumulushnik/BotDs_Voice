"""Хранилище состояния временных комнат.

Данные держатся в памяти и сохраняются в JSON-файл после каждого изменения,
поэтому после перезапуска бот помнит владельцев, баны и панели управления.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Room:
    channel_id: int
    guild_id: int
    owner_id: int
    banned_ids: Set[int] = field(default_factory=set)
    panel_message_id: int | None = None
    join_order: List[int] = field(default_factory=list)  # кто когда зашёл — для автопередачи владения

    def to_dict(self) -> dict:
        data = asdict(self)
        data["banned_ids"] = sorted(self.banned_ids)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Room:
        return cls(
            channel_id=int(data["channel_id"]),
            guild_id=int(data["guild_id"]),
            owner_id=int(data["owner_id"]),
            banned_ids={int(x) for x in data.get("banned_ids", [])},
            panel_message_id=data.get("panel_message_id"),
            join_order=[int(x) for x in data.get("join_order", [])],
        )


class TriarchRepository:
    def __init__(self, storage_path: Path | None = None) -> None:
        self._rooms: Dict[int, Room] = {}
        # (guild_id, user_id) — кому бот выдал серверный мут и должен его снять.
        self._server_muted: Set[Tuple[int, int]] = set()
        self._path = storage_path
        self._load()

    # ---------- чтение ----------

    def get(self, channel_id: int) -> Room | None:
        return self._rooms.get(channel_id)

    def is_temp_room(self, channel_id: int) -> bool:
        return channel_id in self._rooms

    def get_owner_id(self, channel_id: int) -> int | None:
        room = self._rooms.get(channel_id)
        return room.owner_id if room else None

    def room_ids(self) -> List[int]:
        return list(self._rooms)

    def rooms_in_guild(self, guild_id: int) -> List[Room]:
        return [room for room in self._rooms.values() if room.guild_id == guild_id]

    def find_by_owner(self, guild_id: int, owner_id: int) -> Room | None:
        for room in self._rooms.values():
            if room.guild_id == guild_id and room.owner_id == owner_id:
                return room
        return None

    def join_order(self, channel_id: int) -> List[int]:
        room = self._rooms.get(channel_id)
        return list(room.join_order) if room else []

    def is_banned(self, channel_id: int, user_id: int) -> bool:
        room = self._rooms.get(channel_id)
        return bool(room and user_id in room.banned_ids)

    def banned_ids(self, channel_id: int) -> Set[int]:
        room = self._rooms.get(channel_id)
        return set(room.banned_ids) if room else set()

    # ---------- запись ----------

    def register_room(self, channel_id: int, guild_id: int, owner_id: int) -> Room:
        room = Room(channel_id=channel_id, guild_id=guild_id, owner_id=owner_id)
        self._rooms[channel_id] = room
        self._save()
        return room

    def remove_room(self, channel_id: int) -> None:
        if self._rooms.pop(channel_id, None) is not None:
            self._save()

    def set_owner_id(self, channel_id: int, owner_id: int) -> None:
        room = self._rooms.get(channel_id)
        if room:
            room.owner_id = owner_id
            self._save()

    def set_panel_message(self, channel_id: int, message_id: int) -> None:
        room = self._rooms.get(channel_id)
        if room:
            room.panel_message_id = message_id
            self._save()

    def mark_joined(self, channel_id: int, user_id: int) -> None:
        room = self._rooms.get(channel_id)
        if room and user_id not in room.join_order:
            room.join_order.append(user_id)
            self._save()

    def mark_left(self, channel_id: int, user_id: int) -> None:
        room = self._rooms.get(channel_id)
        if room and user_id in room.join_order:
            room.join_order.remove(user_id)
            self._save()

    def ban_user(self, channel_id: int, user_id: int) -> None:
        room = self._rooms.get(channel_id)
        if room:
            room.banned_ids.add(user_id)
            self._save()

    def unban_user(self, channel_id: int, user_id: int) -> None:
        room = self._rooms.get(channel_id)
        if room:
            room.banned_ids.discard(user_id)
            self._save()

    # ---------- серверный мут, выданный ботом

    def is_server_muted(self, guild_id: int, user_id: int) -> bool:
        return (guild_id, user_id) in self._server_muted

    def server_muted_in(self, guild_id: int) -> List[int]:
        return [uid for gid, uid in self._server_muted if gid == guild_id]

    def add_server_muted(self, guild_id: int, user_id: int) -> None:
        if (guild_id, user_id) not in self._server_muted:
            self._server_muted.add((guild_id, user_id))
            self._save()

    def remove_server_muted(self, guild_id: int, user_id: int) -> None:
        if (guild_id, user_id) in self._server_muted:
            self._server_muted.discard((guild_id, user_id))
            self._save()

    # ---------- персистентность ----------

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for item in raw.get("rooms", []):
                room = Room.from_dict(item)
                self._rooms[room.channel_id] = room
            self._server_muted = {(int(g), int(u)) for g, u in raw.get("server_muted", [])}
            logger.info("Загружено комнат из %s: %d", self._path, len(self._rooms))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("Файл состояния %s повреждён, начинаем с пустого: %s", self._path, exc)
            self._rooms.clear()
            self._server_muted.clear()

    def _save(self) -> None:
        if not self._path:
            return
        payload = {
            "rooms": [room.to_dict() for room in self._rooms.values()],
            "server_muted": sorted([g, u] for g, u in self._server_muted),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Атомарная запись: сначала во временный файл, затем замена.
            fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except OSError as exc:
            logger.error("Не удалось сохранить состояние в %s: %s", self._path, exc)
