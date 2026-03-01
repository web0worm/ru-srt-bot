#!/usr/bin/env python3
"""
Хранилище пользователей бота.
Сохраняет user_id всех, кто взаимодействовал с ботом.
"""
import json
import os
from pathlib import Path
from typing import Set

from ..config import Settings


def _users_file(settings: Settings) -> Path:
    return Path(settings.state_file).parent / "users.json"


def load_user_ids(settings: Settings) -> Set[int]:
    """Загружает множество user_id из файла."""
    path = _users_file(settings)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("user_ids", []))
    except Exception:
        return set()


def save_user_ids(user_ids: Set[int], settings: Settings) -> None:
    """Сохраняет множество user_id в файл."""
    path = _users_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"user_ids": sorted(user_ids)}, f)
    os.replace(tmp, path)


def track_user(user_id: int, settings: Settings) -> None:
    """Добавляет user_id в хранилище (если ещё нет)."""
    users = load_user_ids(settings)
    if user_id not in users:
        users.add(user_id)
        save_user_ids(users, settings)
