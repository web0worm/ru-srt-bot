#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from app.config import load_settings
from app.core import storage
from app.core.models import StreamStatus

def check_long_running_tunnels():
    settings = load_settings()
    state = storage.load_state(settings)
    now = datetime.now(timezone.utc)
    threshold = timedelta(hours=24)
    results = []
    
    for inc in state.incoming_streams:
        if inc.status != StreamStatus.RUNNING or not inc.start_time:
            continue
        
        try:
            # start_time может быть float (unix timestamp) или ISO строкой
            if isinstance(inc.start_time, (int, float)):
                start_dt = datetime.fromtimestamp(float(inc.start_time), tz=timezone.utc)
            else:
                start_dt = datetime.fromisoformat(str(inc.start_time).replace('Z', '+00:00'))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
            duration = now - start_dt
            
            if duration >= threshold:
                reminder_file = Path(settings.state_file).parent / f"reminder_{inc.user_id}.json"
                should_remind = True
                
                if reminder_file.exists():
                    try:
                        data = json.loads(reminder_file.read_text(encoding='utf-8'))
                        last = datetime.fromisoformat(data.get('last_reminder', '2000-01-01T00:00:00+00:00'))
                        if (now - last) < timedelta(hours=12):
                            should_remind = False
                    except:
                        pass
                
                if should_remind:
                    hours = int(duration.total_seconds() / 3600)
                    results.append((inc.user_id, inc.id, hours))
        except:
            continue
    
    return results

if __name__ == '__main__':
    tunnels = check_long_running_tunnels()
    for user_id, stream_id, hours in tunnels:
        print(f"{user_id}:{stream_id}:{hours}")
