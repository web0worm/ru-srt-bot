import logging
import os
import re
import signal
import subprocess
import time
from typing import Optional, List, Tuple

from .server_manager import stop_stream_on_server, read_remote_file
from .server_config import get_server_by_id
from .models import IncomingStream, OutgoingStream, StreamStatus
from .storage import save_state
from ..config import Settings

logger = logging.getLogger(__name__)

MULTICAST_ADDR = "239.0.0.1"


def build_srt_listener_url(port: int, passphrase: Optional[str], latency: int = 120) -> str:
    """
    Общий конструктор для SRT-listener URL (и для входящих, и для исходящих).
    ffmpeg в режиме listener: удалённый caller коннектится к этому порту.
    """
    base = f"srt://0.0.0.0:{port}?mode=listener&transtype=live&latency={latency}&tlpktdrop=1&nakreport=1&rcvbuf=12058624&sndbuf=12058624&oheadbw=25&maxbw=0"
    if passphrase:
        base += f"&pbkeylen=16&passphrase={passphrase}"
    return base


def start_incoming_ffmpeg(
    stream: IncomingStream,
    settings: Settings,
    state,
) -> None:
    """
    Входящий поток:
    - ffmpeg слушает SRT на local_port_in;
    - отправляет MPEGTS в мультикаст udp://239.0.0.1:internal_port.
    """
    logs_dir = settings.logs_dir
    os.makedirs(logs_dir, exist_ok=True)

    log_path = os.path.join(logs_dir, f"in_{stream.id}.log")

    srt_url = build_srt_listener_url(
        stream.local_port_in,
        stream.passphrase_in,
        stream.latency_in,
    )

    # Внутренний мультикаст-адрес, чтобы несколько исходящих могли читать один и тот же поток.
    # Без огромных fifo_size и overrun_nonfatal, чтобы не раздувать память.
    udp_url = f"udp://{MULTICAST_ADDR}:{stream.internal_port}?pkt_size=1316&ttl=1&reuse=1"

    stats_path = os.path.join(logs_dir, f"in_{stream.id}.stats")

    cmd = [
        "ffmpeg",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "5000000",
        "-analyzeduration", "3000000",
        "-loglevel", "info",
        "-nostats",
        "-progress", f"file:{stats_path}",
        "-i",
        srt_url,
        "-map", "0",
        "-c",
        "copy",
        "-f",
        "mpegts",
        udp_url,
    ]

    with open(log_path, "a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )

    stream.pid = process.pid
    stream.log_path = log_path
    stream.status = StreamStatus.RUNNING
    stream.start_time = time.time()
    stream.stop_time = None

    save_state(state, settings)


def start_outgoing_ffmpeg(
    incoming_stream: IncomingStream,
    outgoing_stream: OutgoingStream,
    settings: Settings,
    state,
) -> None:
    """
    Исходящий поток:
    - читает из мультикаста udp://239.0.0.1:internal_port (reuse=1);
    - слушает SRT на local_port_out (listener), к которому коннектится приёмник (caller).
    """
    logs_dir = settings.logs_dir
    os.makedirs(logs_dir, exist_ok=True)

    log_path = os.path.join(logs_dir, f"out_{outgoing_stream.id}.log")

    # Убрали overrun_nonfatal и fifo_size=50000000 — они сильно раздували память.
    udp_url = f"udp://{MULTICAST_ADDR}:{incoming_stream.internal_port}?localaddr=239.0.0.1&reuse=1"

    srt_url = build_srt_listener_url(
        outgoing_stream.local_port_out,
        outgoing_stream.passphrase_out,
        outgoing_stream.latency_out,
    )

    stats_path = os.path.join(logs_dir, f"out_{outgoing_stream.id}.stats")

    cmd = [
        "ffmpeg",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "1000000",
        "-analyzeduration", "1000000",
        "-loglevel", "info",
        "-nostats",
        "-progress", f"file:{stats_path}",
        "-i",
        udp_url,
        "-map", "0",
        "-c",
        "copy",
        "-f",
        "mpegts",
        srt_url,
    ]

    with open(log_path, "a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )

    outgoing_stream.pid = process.pid
    outgoing_stream.log_path = log_path
    outgoing_stream.status = StreamStatus.RUNNING
    outgoing_stream.start_time = time.time()
    outgoing_stream.stop_time = None

    save_state(state, settings)


def stop_process(pid: Optional[int], timeout: float = 3.0) -> None:
    """
    Аккуратная остановка процесса:
    - SIGTERM и ожидание до timeout;
    - если жив — SIGKILL.
    """
    if not pid:
        return

    try:
        # Сначала мягко
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Процесс уже умер
        return
    except Exception:
        return

    # Ждём до timeout, пока процесс не умрёт
    start = time.time()
    while time.time() - start < timeout:
        try:
            # Проверяем, жив ли процесс
            os.kill(pid, 0)
        except ProcessLookupError:
            # Уже нет
            return
        except Exception:
            return
        time.sleep(0.1)

    # Если всё ещё жив — добиваем SIGKILL
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        pass


def stop_incoming_stream(
    stream: IncomingStream,
    settings: Settings,
    state,
) -> None:
    # Получаем конфигурацию сервера для потока
    # Используем getattr для безопасного получения server_id
    # Определяем server_id - используем из потока или определяем по порту
    from .server_config import get_server_id_by_port
    stream_server_id = get_server_id_by_port(stream.local_port_in)
    server_config = get_server_by_id(stream_server_id)
    
    # Сначала стопаем все исходящие
    # ВАЖНО: исходящие ВСЕГДА на том же сервере что и входящий (нужен мультикаст)
    # Поэтому используем server_config входящего потока, а НЕ o.server_id
    for o in stream.outgoing_streams:
        if o.status == StreamStatus.RUNNING:
            if server_config:
                print(f"[STOP] Stopping outgoing {o.id} port={o.local_port_out} on {server_config.name} ({server_config.host})")
                stop_stream_on_server(server_config, o.pid, port=o.local_port_out)
            else:
                stop_process(o.pid)
            o.status = StreamStatus.STOPPED
            o.stop_time = time.time()

    # Потом сам входящий
    if stream.status == StreamStatus.RUNNING:
        print(f"Attempting to stop stream {stream.id}: port={stream.local_port_in}, server_id={stream_server_id}, pid={stream.pid}, server_config={server_config.name if server_config else 'None'}")
        if not stream.pid:
            logger.warning(f"Stream {stream.id} has no PID, cannot stop")
        elif server_config:
            print(f"Stopping stream {stream.id} on server {server_config.name} (is_local={server_config.is_local}, PID: {stream.pid})")
            result = stop_stream_on_server(server_config, stream.pid, port=stream.local_port_in)
            print(f"Stop result for stream {stream.id}: {result}")
        else:
            logger.warning(f"Server config not found for stream {stream.id}, using fallback stop_process")
            stop_process(stream.pid)  # Fallback на старый способ
        stream.status = StreamStatus.STOPPED
        stream.stop_time = time.time()
        print(f"Stream {stream.id} status set to STOPPED")

    save_state(state, settings)


def stop_outgoing_stream(
    incoming_stream: IncomingStream,
    outgoing_stream: OutgoingStream,
    settings: Settings,
    state,
) -> None:
    if outgoing_stream.status == StreamStatus.RUNNING:
        # Исходящий поток ВСЕГДА на том же сервере что и входящий (нужен мультикаст)
        # Определяем сервер по порту входящего потока
        from .server_config import get_server_id_by_port
        srv_id = get_server_id_by_port(incoming_stream.local_port_in)
        
        server = get_server_by_id(srv_id)
        if server:
            stop_stream_on_server(server, outgoing_stream.pid, port=outgoing_stream.local_port_out)
        else:
            stop_process(outgoing_stream.pid)
        outgoing_stream.status = StreamStatus.STOPPED
        outgoing_stream.stop_time = time.time()

    save_state(state, settings)

def restart_running_streams(settings: Settings, state) -> None:
    """
    При старте бота перезапускаем все потоки,
    которые в state.json помечены как running:
    - входящие;
    - их исходящие.
    Учитывает server_id для мультисерверной архитектуры.
    """
    from .server_config import get_server_by_id
    from .server_manager import start_stream_on_server
    
    for incoming in state.incoming_streams:
        if incoming.status == StreamStatus.RUNNING:
            from .server_config import get_server_id_by_port
            server_id = get_server_id_by_port(incoming.local_port_in)
            
            # Обновляем server_id в потоке если он отличается
            if getattr(incoming, 'server_id', None) != server_id:
                incoming.server_id = server_id
            
            server_config = get_server_by_id(server_id)
            
            if server_config:
                # Используем start_stream_on_server для правильного сервера
                stream_config = {
                    "id": incoming.id,
                    "local_port_in": incoming.local_port_in,
                    "internal_port": incoming.internal_port,
                    "passphrase_in": incoming.passphrase_in,
                    "latency_in": incoming.latency_in,
                }
                success, pid, log_path = start_stream_on_server(
                    server_config,
                    "incoming",
                    stream_config,
                    settings.logs_dir
                )
                if success:
                    incoming.pid = pid
                    incoming.log_path = log_path
                    print(f"Restarted incoming stream {incoming.id} on {server_config.name} (PID: {pid})")
                else:
                    print(f"Failed to restart incoming stream {incoming.id} on {server_config.name}")
            else:
                # Fallback на старый способ для локального сервера
                start_incoming_ffmpeg(incoming, settings, state)
            
            # Исходящие потоки - используем server_id от входящего потока
            for outgoing in incoming.outgoing_streams:
                if outgoing.status == StreamStatus.RUNNING:
                    # Исходящий поток должен быть на том же сервере что и входящий
                    outgoing.server_id = server_id  # Корректируем server_id
                    out_server = get_server_by_id(server_id)
                    
                    if out_server:
                        out_config = {
                            "id": outgoing.id,
                            "local_port_out": outgoing.local_port_out,
                            "remote_host_out": outgoing.remote_host_out,
                            "remote_port_out": outgoing.remote_port_out,
                            "passphrase_out": outgoing.passphrase_out,
                            "latency_out": outgoing.latency_out,
                            "internal_port": incoming.internal_port,
                        }
                        success, pid, log_path = start_stream_on_server(
                            out_server,
                            "outgoing",
                            out_config,
                            settings.logs_dir
                        )
                        if success:
                            outgoing.pid = pid
                            outgoing.log_path = log_path
                            print(f"Restarted outgoing stream {outgoing.id} on {out_server.name} (PID: {pid})")
                    else:
                        start_outgoing_ffmpeg(incoming, outgoing, settings, state)
    
    # Сохраняем обновленное состояние
    save_state(state, settings)


# ========================== СТАТИСТИКА ==========================

def parse_stream_stats(log_content: str, stats_content: str) -> dict:
    """
    Парсит лог FFmpeg и файл прогресса для извлечения статистики.

    log_content  — основной лог (stderr FFmpeg, -loglevel info)
    stats_content — файл -progress (key=value блоки)
    """
    stats = {
        'resolution': None,
        'fps': None,
        'codec': None,
        'bitrate': None,
        'total_frames': 0,
        'drop_frames': 0,
        'dup_frames': 0,
        'total_size_bytes': 0,
        'duration': None,
        'speed': None,
        'restarts': 0,
    }

    # === Лог: разрешение, FPS, кодек ===
    if log_content:
        stats['restarts'] = max(log_content.count('Starting ffmpeg...') - 1, 0)
        for line in log_content.split('\n'):
            if 'Stream #' in line and 'Video:' in line:
                m = re.search(r'(\d{2,5}x\d{2,5})', line)
                if m:
                    stats['resolution'] = m.group(1)
                m = re.search(r'([\d.]+)\s*(?:fps|tbr)', line)
                if m:
                    stats['fps'] = m.group(1)
                m = re.search(r'Video:\s+(\w+)', line)
                if m:
                    stats['codec'] = m.group(1)

    # === Progress-файл: кадры, битрейт, размер, дропы ===
    if stats_content:
        blocks = []
        cur = {}
        for line in stats_content.split('\n'):
            line = line.strip()
            if '=' in line:
                k, _, v = line.partition('=')
                cur[k] = v
            if line.startswith('progress='):
                if cur:
                    blocks.append(cur)
                    cur = {}
        if cur:
            blocks.append(cur)

        # Суммируем с учётом перезапусков (frame сбрасывается в 0)
        total_frames = 0
        total_size = 0
        total_drops = 0
        total_dups = 0
        prev_frame = 0
        max_f = 0
        max_s = 0
        max_d = 0
        max_dup = 0

        for b in blocks:
            f = int(b.get('frame', 0) or 0)
            s = int(b.get('total_size', 0) or 0)
            d = int(b.get('drop_frames', 0) or 0)
            dp = int(b.get('dup_frames', 0) or 0)
            if f < max_f:  # FFmpeg перезапустился — сохраняем итоги прошлого сегмента
                total_frames += max_f
                total_size += max_s
                total_drops += max_d
                total_dups += max_dup
                max_f = max_s = max_d = max_dup = 0
            max_f = max(max_f, f)
            max_s = max(max_s, s)
            max_d = max(max_d, d)
            max_dup = max(max_dup, dp)

        total_frames += max_f
        total_size += max_s
        total_drops += max_d
        total_dups += max_dup

        stats['total_frames'] = total_frames
        stats['total_size_bytes'] = total_size
        stats['drop_frames'] = total_drops
        stats['dup_frames'] = total_dups

        # Из последнего блока берём мгновенные значения
        if blocks:
            last = blocks[-1]
            br = last.get('bitrate', '')
            if br and br != 'N/A':
                stats['bitrate'] = br
            t = last.get('out_time', '')
            if t:
                stats['duration'] = t.split('.')[0] if '.' in t else t
            sp = last.get('speed', '')
            if sp:
                stats['speed'] = sp
            if not stats['fps'] and last.get('fps'):
                stats['fps'] = last['fps']

    return stats


def _size_human(b: int) -> str:
    """Байты → читаемый формат (МБ + Мбит)"""
    if b <= 0:
        return "0"
    mb = b / (1024 * 1024)
    mbit = b * 8 / 1_000_000
    if mb >= 1024:
        return f"{mb / 1024:.2f} ГБ ({mbit / 1000:.2f} Гбит)"
    return f"{mb:.1f} МБ ({mbit:.1f} Мбит)"


def format_stream_stats(
    name: str,
    port_in: int,
    server_name: str,
    incoming_stats: dict,
    outgoing_list: List[Tuple[int, dict]],
) -> str:
    """Формирует красивое сообщение со статистикой потока."""
    lines = [f"📊 Статистика потока «{name}»\n"]

    # --- Входящий ---
    lines.append(f"📥 Вход (SRT :{port_in}, {server_name}):")
    res = incoming_stats.get('resolution')
    fps = incoming_stats.get('fps')
    codec = incoming_stats.get('codec', '')
    if res:
        lines.append(f"  📐 {res} @ {fps or '?'} fps" + (f" ({codec})" if codec else ""))
    elif fps:
        lines.append(f"  📐 ? @ {fps} fps")
    br = incoming_stats.get('bitrate')
    if br:
        lines.append(f"  📈 Битрейт: {br}")
    dur = incoming_stats.get('duration')
    if dur:
        lines.append(f"  🕐 Длительность: {dur}")
    lines.append(f"  📦 Принято: {_size_human(incoming_stats.get('total_size_bytes', 0))}")
    lines.append(f"  🎞 Кадров: {incoming_stats.get('total_frames', 0)}")
    lines.append(f"  ❌ Дропов: {incoming_stats.get('drop_frames', 0)}")
    restarts = incoming_stats.get('restarts', 0)
    if restarts > 0:
        lines.append(f"  🔄 Реконнектов: {restarts}")

    # --- Исходящие ---
    for i, (port, st) in enumerate(outgoing_list, 1):
        lines.append(f"\n📤 Выход #{i} (SRT :{port}):")
        br2 = st.get('bitrate')
        if br2:
            lines.append(f"  📈 Битрейт: {br2}")
        lines.append(f"  📦 Отправлено: {_size_human(st.get('total_size_bytes', 0))}")
        lines.append(f"  🎞 Кадров: {st.get('total_frames', 0)}")
        lines.append(f"  ❌ Дропов: {st.get('drop_frames', 0)}")
        r2 = st.get('restarts', 0)
        if r2 > 0:
            lines.append(f"  🔄 Реконнектов: {r2}")

    return "\n".join(lines)


def collect_stream_stats_data(incoming: IncomingStream, settings) -> dict:
    """
    Собирает статистику входящего потока + всех его исходящих.
    Возвращает словарь с данными для форматирования.
    """
    from .server_config import get_server_id_by_port

    result = {
        'server_name': '',
        'incoming': {},
        'outgoing': [],
    }
    try:
        sid = get_server_id_by_port(incoming.local_port_in)
        server = get_server_by_id(sid)
        result['server_name'] = server.name if server else sid

        def _read(path):
            if not path:
                return ""
            if server and not server.is_local:
                return read_remote_file(server, path)
            try:
                with open(path, 'r', errors='replace') as fh:
                    return fh.read()
            except Exception:
                return ""

        # Входящий
        log_c = _read(incoming.log_path)
        stats_path = incoming.log_path.replace('.log', '.stats') if incoming.log_path else ""
        stats_c = _read(stats_path)
        result['incoming'] = parse_stream_stats(log_c, stats_c)

        # Исходящие
        for o in incoming.outgoing_streams:
            o_log = _read(o.log_path)
            o_sp = o.log_path.replace('.log', '.stats') if o.log_path else ""
            o_st = _read(o_sp)
            result['outgoing'].append((o.local_port_out, parse_stream_stats(o_log, o_st)))

    except Exception as e:
        logger.error(f"Error collecting stats data: {e}")

    return result


def format_deletion_message(
    name: str,
    owner: str,
    port_in: int,
    server_name: str,
    start_str: str,
    stop_str: str,
    duration: str,
    incoming_stats: dict,
    outgoing_list: List[Tuple[int, dict]],
    support_text: str,
) -> str:
    """
    Формирует единое красивое сообщение при удалении потока:
    статистика + мета-информация + призыв к поддержке.
    """
    lines = [f"✅ Поток «{name}» удалён"]
    lines.append(f"👤 {owner}")
    lines.append(f"📅 {start_str} → {stop_str}")
    lines.append(f"⏱ {duration}")

    # --- Входящий ---
    lines.append(f"\n📥 Вход (SRT :{port_in}, {server_name}):")
    res = incoming_stats.get('resolution')
    fps = incoming_stats.get('fps')
    codec = incoming_stats.get('codec', '')
    if res:
        lines.append(f"  📐 {res} @ {fps or '?'} fps" + (f" ({codec})" if codec else ""))
    elif fps:
        lines.append(f"  📐 ? @ {fps} fps")
    br = incoming_stats.get('bitrate')
    if br:
        lines.append(f"  📈 Битрейт: {br}")
    lines.append(f"  📦 Принято: {_size_human(incoming_stats.get('total_size_bytes', 0))}")
    lines.append(f"  🎞 Кадров: {incoming_stats.get('total_frames', 0)}")
    lines.append(f"  ❌ Дропов: {incoming_stats.get('drop_frames', 0)}")
    restarts = incoming_stats.get('restarts', 0)
    if restarts > 0:
        lines.append(f"  🔄 Реконнектов: {restarts}")

    # --- Исходящие ---
    for i, (port, st) in enumerate(outgoing_list, 1):
        lines.append(f"\n📤 Выход #{i} (SRT :{port}):")
        br2 = st.get('bitrate')
        if br2:
            lines.append(f"  📈 Битрейт: {br2}")
        lines.append(f"  📦 Отправлено: {_size_human(st.get('total_size_bytes', 0))}")
        lines.append(f"  🎞 Кадров: {st.get('total_frames', 0)}")
        lines.append(f"  ❌ Дропов: {st.get('drop_frames', 0)}")
        r2 = st.get('restarts', 0)
        if r2 > 0:
            lines.append(f"  🔄 Реконнектов: {r2}")

    lines.append(f"\n☕ {support_text}")

    return "\n".join(lines)
