#!/usr/bin/env python3
"""
Очищает зомби-процессы ffmpeg, которые не принадлежат ни одному управляемому потоку.
Запускается по крону каждые 5 минут.
Использует ДВОЙНУЮ проверку:
  1. Дерево процессов (PID потомки wrapper-ов из state.json)
  2. Порты (если в cmdline процесса есть порт активного потока — не трогаем)
"""
import json
import subprocess
import os
import re
from pathlib import Path
from typing import Set

BASE_DIR = Path("/opt/srt-bot")
STATE_FILE = BASE_DIR / "data" / "state.json"


def load_state_data() -> dict:
    """Загружает state.json"""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_allowed_pids_and_ports(raw: dict) -> tuple[Set[int], Set[str]]:
    """
    Из state.json собираем:
      - PID-ы wrapper процессов (для дерева)
      - Порты активных потоков (для cmd-line matching)
    """
    pids: Set[int] = set()
    ports: Set[str] = set()

    for s in raw.get("incoming_streams", []):
        if s.get("status") == "running":
            pid = s.get("pid")
            if isinstance(pid, int) and pid > 0:
                pids.add(pid)
            port = s.get("local_port_in")
            if port:
                ports.add(str(port))
            # internal_port тоже добавляем
            iport = s.get("internal_port")
            if iport:
                ports.add(str(iport))

        for o in s.get("outgoing_streams", []):
            if o.get("status") == "running":
                pid = o.get("pid")
                if isinstance(pid, int) and pid > 0:
                    pids.add(pid)
                port = o.get("local_port_out")
                if port:
                    ports.add(str(port))

    return pids, ports


def get_all_descendants(pid: int) -> Set[int]:
    """Рекурсивно получает все дочерние процессы."""
    descendants = set()
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                child_pid = int(line.strip())
                descendants.add(child_pid)
                descendants.update(get_all_descendants(child_pid))
    except Exception:
        pass
    return descendants


def get_cmdline(pid: int) -> str:
    """Читает cmdline процесса."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", errors="replace").replace("\x00", " ")
    except Exception:
        return ""


def process_uses_managed_port(pid: int, managed_ports: Set[str]) -> bool:
    """Проверяет, содержит ли cmdline процесса порт активного потока."""
    cmd = get_cmdline(pid)
    if not cmd:
        return False
    for port in managed_ports:
        # Ищем порт в cmdline: "...:{port}?" или "...:{port} " или "...:{port}&"
        if f":{port}?" in cmd or f":{port} " in cmd or f":{port}&" in cmd or cmd.endswith(f":{port}"):
            return True
    return False


def main():
    raw = load_state_data()
    allowed_pids, managed_ports = get_allowed_pids_and_ports(raw)

    # Расширяем allowed: добавляем всех потомков
    allowed_tree = set(allowed_pids)
    for wpid in allowed_pids:
        allowed_tree.update(get_all_descendants(wpid))

    # Ищем все ffmpeg процессы
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ffmpeg"],
            capture_output=True, text=True, timeout=5
        )
    except Exception:
        return

    found_pids = set()
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            try:
                found_pids.add(int(line.strip()))
            except ValueError:
                pass

    if not found_pids:
        return

    # Фильтруем: оставляем только реально чужие процессы
    stray = found_pids - allowed_tree

    real_stray = set()
    for pid in stray:
        cmd = get_cmdline(pid)

        # Пропускаем себя и pgrep
        if "cleanup_ffmpeg" in cmd or "pgrep" in cmd:
            continue

        # Проверка по порту: если процесс использует порт активного потока — не трогаем
        if process_uses_managed_port(pid, managed_ports):
            continue

        real_stray.add(pid)

    if real_stray:
        print(f"Found ffmpeg PIDs: {sorted(found_pids)}")
        print(f"Allowed tree: {sorted(allowed_tree)}")
        print(f"Managed ports: {sorted(managed_ports)}")
        print(f"Killing stray PIDs: {sorted(real_stray)}")
        for pid in real_stray:
            try:
                os.kill(pid, 9)
                print(f"  Killed PID {pid}")
            except ProcessLookupError:
                pass
            except Exception as e:
                print(f"  Error killing PID {pid}: {e}")
    else:
        if found_pids:
            print(f"All {len(found_pids)} ffmpeg processes are managed. OK.")


if __name__ == "__main__":
    main()
