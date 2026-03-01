import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

from .models import IncomingStream, OutgoingStream, StreamStatus
from ..config import Settings


@dataclass
class AppState:
    incoming_streams: List[IncomingStream] = field(default_factory=list)


# ===== СЕРИАЛИЗАЦИЯ / ДЕСЕРИАЛИЗАЦИЯ =============================================


def _outgoing_from_dict(d: dict) -> OutgoingStream:
    return OutgoingStream(
        id=d["id"],
        user_id=d["user_id"],
        local_port_out=d["local_port_out"],
        remote_host_out=d["remote_host_out"],
        remote_port_out=d["remote_port_out"],
        passphrase_out=d.get("passphrase_out"),
        latency_out=d.get("latency_out", 120),
        status=StreamStatus(d.get("status", "stopped")),
        pid=d.get("pid"),
        log_path=d.get("log_path"),
        start_time=d.get("start_time"),
        stop_time=d.get("stop_time"),
        server_id=d.get("server_id", "spb"),
        expires_at=d.get("expires_at"),
    )


def _incoming_from_dict(d: dict) -> IncomingStream:
    outgoing_raw = d.get("outgoing_streams", [])
    outgoing_streams = [_outgoing_from_dict(o) for o in outgoing_raw]

    return IncomingStream(
        id=d["id"],
        user_id=d["user_id"],
        name=d["name"],
        local_port_in=d["local_port_in"],
        internal_port=d["internal_port"],
        remote_host_in=d["remote_host_in"],
        remote_port_in=d["remote_port_in"],
        passphrase_in=d.get("passphrase_in"),
        latency_in=d.get("latency_in", 120),
        status=StreamStatus(d.get("status", "stopped")),
        pid=d.get("pid"),
        log_path=d.get("log_path"),
        start_time=d.get("start_time"),
        stop_time=d.get("stop_time"),
        outgoing_streams=outgoing_streams,
        server_id=d.get("server_id", "spb"),
    )


def _outgoing_to_dict(o: OutgoingStream, parent_server_id: str = "spb") -> dict:
    return {
        "id": o.id,
        "user_id": o.user_id,
        "local_port_out": o.local_port_out,
        "remote_host_out": o.remote_host_out,
        "remote_port_out": o.remote_port_out,
        "passphrase_out": o.passphrase_out,
        "latency_out": o.latency_out,
        "status": o.status.value,
        "pid": o.pid,
        "log_path": o.log_path,
        "start_time": o.start_time,
        "stop_time": o.stop_time,
        "server_id": parent_server_id,  # Используем server_id от входящего потока
    }


def _incoming_to_dict(s: IncomingStream) -> dict:
    # Определяем server_id по порту - надежнее чем хранимое значение
    port = s.local_port_in
    if 4000 <= port <= 4100:
        server_id = "msk"
    elif 5000 <= port <= 5100:
        server_id = "spb"
    else:
        server_id = getattr(s, 'server_id', 'spb')
    
    return {
        "id": s.id,
        "user_id": s.user_id,
        "name": s.name,
        "local_port_in": s.local_port_in,
        "internal_port": s.internal_port,
        "remote_host_in": s.remote_host_in,
        "remote_port_in": s.remote_port_in,
        "passphrase_in": s.passphrase_in,
        "latency_in": s.latency_in,
        "status": s.status.value,
        "pid": s.pid,
        "log_path": s.log_path,
        "start_time": s.start_time,
        "stop_time": s.stop_time,
        "server_id": server_id,
        "expires_at": getattr(s, 'expires_at', None),
        "outgoing_streams": [_outgoing_to_dict(o, server_id) for o in s.outgoing_streams],
    }


# ===== ЗАГРУЗКА / СОХРАНЕНИЕ СОСТОЯНИЯ ==========================================


def load_state(settings: Settings) -> AppState:
    """
    Читает JSON-файл состояния и восстанавливает AppState.
    Если файла нет или он повреждён — возвращает пустое состояние.
    """
    path = settings.state_file
    if not os.path.exists(path):
        return AppState()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return AppState()

    incoming_raw = raw.get("incoming_streams", [])
    incoming_streams = []
    for d in incoming_raw:
        try:
            incoming_streams.append(_incoming_from_dict(d))
        except Exception:
            continue

    return AppState(incoming_streams=incoming_streams)


def save_state(state: AppState, settings: Settings) -> None:
    """
    Сохраняет текущее состояние в JSON.
    """
    path = settings.state_file
    os.makedirs(os.path.dirname(path), exist_ok=True)

    raw = {
        "incoming_streams": [_incoming_to_dict(s) for s in state.incoming_streams]
    }

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С ПОТОКАМИ =============================


def get_user_incoming_streams(state: AppState, user_id: int) -> list[IncomingStream]:
    return [s for s in state.incoming_streams if s.user_id == user_id]


def get_incoming_stream_by_id(
    state: AppState, stream_id: str
) -> Optional[IncomingStream]:
    for s in state.incoming_streams:
        if s.id == stream_id:
            return s
    return None


def count_running_incoming_streams(state: AppState) -> int:
    """
    Считает количество входящих потоков в статусе RUNNING.
    Именно по этому числу применяется лимит max_incoming_streams.
    """
    return sum(1 for s in state.incoming_streams if s.status == StreamStatus.RUNNING)


def allocate_incoming_port(state: AppState, settings: Settings, server_id: str = "spb") -> Optional[int]:
    """
    Выбирает свободный порт для входящего потока из диапазона портов для указанного сервера.
    Учитывает server_id при проверке занятых портов.
    """
    from ..core.server_config import get_server_by_id
    
    server_config = get_server_by_id(server_id)
    if not server_config:
        return None
    
    # Используем диапазон портов для указанного сервера
    start_port = server_config.incoming_port_start
    end_port = server_config.incoming_port_end
    
    # Проверяем ВСЕ занятые порты в диапазоне сервера (независимо от server_id)
    used_ports = {
        s.local_port_in
        for s in state.incoming_streams
        if start_port <= s.local_port_in <= end_port
    }
    import logging
    logging.getLogger(__name__).info(f"allocate_incoming_port: server_id={server_id}, range={start_port}-{end_port}, used_ports={used_ports}, streams_count={len(state.incoming_streams)}")

    for port in range(start_port, end_port + 1):
        if port not in used_ports:
            return port

    # Нет свободных портов в диапазоне
    return None


def allocate_outgoing_port(state: AppState, settings: Settings, server_id: str = "spb") -> Optional[int]:
    """
    Выбирает свободный порт для исходящего потока из диапазона портов указанного сервера.
    """
    from ..core.server_config import get_server_by_id
    
    server_config = get_server_by_id(server_id)
    if server_config:
        start_port = server_config.outgoing_port_start
        end_port = server_config.outgoing_port_end
    else:
        # Fallback на settings
        start_port, end_port = settings.outgoing_port_range

    # Собираем ВСЕ занятые порты в этом диапазоне (независимо от server_id потока)
    used_ports = set()
    for inc in state.incoming_streams:
        for out in inc.outgoing_streams:
            # Проверяем все порты в диапазоне данного сервера
            if start_port <= out.local_port_out <= end_port:
                used_ports.add(out.local_port_out)

    for port in range(start_port, end_port + 1):
        if port not in used_ports:
            return port

    return None
