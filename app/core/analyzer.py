import os
import re
import time
from typing import Dict, Optional, List


def _parse_single_log(lines: List[str]) -> Dict[str, str]:
    """
    Парсит один лог ffmpeg, возвращает частичную статистику:
    - resolution
    - avg_bitrate
    - dropped_frames
    - frames (если удаётся)
    - fps/time (для возможной оценки кадров)
    """
    res: Dict[str, str] = {}

    if not lines:
        return res

    # --- Разрешение (ищем сверху) ---
    for line in lines:
        m = re.search(r"(\d{3,5})x(\d{3,5})", line)
        if m:
            w, h = m.group(1), m.group(2)
            res["resolution"] = f"{w}x{h}"
            break

    # --- Битрейт (ищем снизу) ---
    for line in reversed(lines):
        m = re.search(r"bitrate=\s*([\d\.]+kbits/s)", line)
        if m:
            res["avg_bitrate"] = m.group(1)
            break

    # --- Dropped frames (ищем снизу) ---
    for line in reversed(lines):
        m = re.search(r"drop=\s*(\d+)", line)
        if m:
            res["dropped_frames"] = m.group(1)
            break

    # --- Попытка вытащить кадры напрямую из frame=/frame:/frames= ---
    frame_patterns = [
        re.compile(r"frame=\s*([0-9]+)"),     # frame=  123 / frame=123
        re.compile(r"frame:\s*([0-9]+)"),     # frame: 123
        re.compile(r"frames=\s*([0-9]+)"),    # frames=123
    ]

    max_frame: Optional[int] = None
    for line in lines:
        for pat in frame_patterns:
            m = pat.search(line)
            if m:
                val = int(m.group(1))
                if max_frame is None or val > max_frame:
                    max_frame = val

    # --- Если frame=/frames= не найдено, пробуем оценить по fps * time ---
    last_fps: Optional[float] = None
    last_time_seconds: Optional[float] = None

    for line in lines:
        # fps=XX.X
        m_fps = re.search(r"fps=\s*([\d\.]+)", line)
        if m_fps:
            try:
                last_fps = float(m_fps.group(1))
            except ValueError:
                pass

        # time=HH:MM:SS.xx
        m_time = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
        if m_time:
            try:
                hh = int(m_time.group(1))
                mm = int(m_time.group(2))
                ss = float(m_time.group(3))
                last_time_seconds = hh * 3600 + mm * 60 + ss
            except ValueError:
                pass

    if max_frame is None and last_fps is not None and last_time_seconds is not None:
        est = int(last_fps * last_time_seconds)
        if est >= 0:
            max_frame = est

    if max_frame is not None:
        res["frames"] = str(max_frame)

    # Служебно возвращаем fps/time, если пригодится
    if last_fps is not None:
        res["_last_fps"] = str(last_fps)
    if last_time_seconds is not None:
        res["_last_time_seconds"] = str(last_time_seconds)

    return res


def parse_ffmpeg_logs(log_paths: List[str]) -> Dict[str, str]:
    """
    Агрегирует статистику по НЕСКОЛЬКИМ логам ffmpeg.
    Используем для входящего + всех исходящих.

    Правила:
    - resolution: первое ненулевое найденное
    - avg_bitrate: последнее найденное
    - dropped_frames: последнее найденное
    - frames: максимальное найденное по всем логам
    """
    result: Dict[str, str] = {}

    max_frames: Optional[int] = None
    last_bitrate: Optional[str] = None
    last_dropped: Optional[str] = None
    first_resolution: Optional[str] = None

    for path in log_paths:
        if not path or not os.path.exists(path):
            continue

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            continue

        partial = _parse_single_log(lines)

        # resolution
        if not first_resolution and "resolution" in partial:
            first_resolution = partial["resolution"]

        # bitrate
        if "avg_bitrate" in partial:
            last_bitrate = partial["avg_bitrate"]

        # dropped
        if "dropped_frames" in partial:
            last_dropped = partial["dropped_frames"]

        # frames
        if "frames" in partial:
            try:
                val = int(partial["frames"])
                if max_frames is None or val > max_frames:
                    max_frames = val
            except ValueError:
                pass

    if first_resolution:
        result["resolution"] = first_resolution
    if last_bitrate:
        result["avg_bitrate"] = last_bitrate
    if last_dropped:
        result["dropped_frames"] = last_dropped
    if max_frames is not None:
        result["frames"] = str(max_frames)

    return result


def parse_ffmpeg_log(log_path: Optional[str]) -> Dict[str, str]:
    """
    Обратная совместимость: парсинг одного лога.
    """
    if not log_path:
        return {}
    return parse_ffmpeg_logs([log_path])


def parse_duration(start_time: Optional[float], stop_time: Optional[float]) -> str:
    """
    Переводит разницу времени в человекочитаемый вид.
    Если stop_time = None — считаем до текущего момента (для running-потока).
    """
    if not start_time:
        return "нет данных"

    end = stop_time or time.time()
    seconds = int(end - start_time)
    if seconds < 0:
        return "нет данных"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{secs} с")

    return " ".join(parts)
