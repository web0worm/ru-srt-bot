#!/usr/bin/env python3
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.config import load_settings
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timezone
import json

async def send_reminder(bot, settings, user_id, stream_id, hours):
    try:
        from app.core import storage
        from app.core.models import StreamStatus
        
        state = storage.load_state(settings)
        incoming = None
        for s in state.incoming_streams:
            if s.id == stream_id and s.user_id == user_id and s.status == StreamStatus.RUNNING:
                incoming = s
                break
        
        if not incoming:
            return
        
        text = (
            f"⚠️ <b>Напоминание о туннеле</b>\n\n"
            f"Ваш туннель <b>{incoming.name}</b> работает уже более {hours} часов.\n\n"
            f"Порт: {incoming.local_port_in}\n"
            f"Хотите остановить туннель и освободить ресурсы?"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Остановить туннель", callback_data=f"tunnel_reminder:stop:{stream_id}")],
            [InlineKeyboardButton(text="✅ Оставить как есть", callback_data=f"tunnel_reminder:keep:{stream_id}")],
        ])
        
        await bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")
        
        reminder_file = Path(settings.state_file).parent / f"reminder_{user_id}.json"
        reminder_file.write_text(json.dumps({
            "last_reminder": datetime.now(timezone.utc).isoformat(),
            "acknowledged": False
        }), encoding='utf-8')
    except Exception as e:
        print(f"Error sending reminder to {user_id}: {e}", file=sys.stderr)

async def main():
    settings = load_settings()
    bot = Bot(token=settings.bot_token)
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            user_id, stream_id, hours = line.split(":")
            await send_reminder(bot, settings, int(user_id), stream_id, int(hours))
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
    
    await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
