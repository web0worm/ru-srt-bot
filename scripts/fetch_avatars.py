#!/usr/bin/env python3
import os
import json
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path("/opt/srt-bot")
STATE_FILE = BASE_DIR / "data" / "state.json"
AVATAR_DIR = BASE_DIR / "avatars"
ENV_FILE = BASE_DIR / ".env"


def load_token() -> str:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    return token


def get_user_ids_from_state() -> set[int]:
    if not STATE_FILE.exists():
        return set()

    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return set()

    user_ids: set[int] = set()
    incoming_streams = raw.get("incoming_streams", [])

    for s in incoming_streams:
        uid = s.get("user_id")
        if uid:
            user_ids.add(int(uid))

        for o in s.get("outgoing_streams", []):
            ouid = o.get("user_id")
            if ouid:
                user_ids.add(int(ouid))

    return user_ids


def fetch_avatar_for_user(api_url: str, user_id: int) -> None:
    # 1. Получаем список фоток профиля
    try:
        resp = requests.get(
            f"{api_url}/getUserProfilePhotos",
            params={"user_id": user_id, "limit": 1},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        print(f"[{user_id}] getUserProfilePhotos error: {e}")
        return

    if not data.get("ok") or data.get("result", {}).get("total_count", 0) == 0:
        print(f"[{user_id}] no profile photos")
        return

    photos = data["result"]["photos"][0]
    # берём самое большое по размеру
    biggest = max(photos, key=lambda p: p.get("file_size", 0))
    file_id = biggest["file_id"]

    # 2. Получаем путь к файлу
    try:
        resp2 = requests.get(
            f"{api_url}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        data2 = resp2.json()
    except Exception as e:
        print(f"[{user_id}] getFile error: {e}")
        return

    if not data2.get("ok"):
        print(f"[{user_id}] getFile failed: {data2}")
        return

    file_path = data2["result"]["file_path"]
    file_url = f"{api_url.replace('/bot', '/file/bot')}/{file_path}"

    # 3. Качаем файл
    try:
        img_resp = requests.get(file_url, timeout=20)
        img_resp.raise_for_status()
    except Exception as e:
        print(f"[{user_id}] download error: {e}")
        return

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    out_path = AVATAR_DIR / f"{user_id}.jpg"
    out_path.write_bytes(img_resp.content)
    print(f"[{user_id}] avatar saved to {out_path}")


def main():
    token = load_token()
    api_url = f"https://api.telegram.org/bot{token}"

    user_ids = get_user_ids_from_state()
    if not user_ids:
        print("No user_ids found in state.json")
        return

    print(f"Found {len(user_ids)} user_ids in state.json")
    for uid in user_ids:
        try:
            fetch_avatar_for_user(api_url, uid)
        except Exception as e:
            print(f"[{uid}] unexpected error: {e}")


if __name__ == "__main__":
    main()
