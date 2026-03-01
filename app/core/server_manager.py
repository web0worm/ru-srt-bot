#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Управление удаленными серверами через SSH
"""
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from .server_config import ServerConfig, get_server_by_id

logger = logging.getLogger(__name__)


def execute_ssh_command(server: ServerConfig, command: str, timeout: int = 30) -> tuple[bool, str]:
    """
    Выполняет команду на удаленном сервере через SSH
    
    Returns:
        (success: bool, output: str)
    """
    if server.is_local:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            logger.error(f"Local command timeout: {command[:80]}")
            return False, "Command timeout"
        except Exception as e:
            logger.error(f"Local command error: {e}")
            return False, str(e)
    
    # Удаленный сервер - через SSH
    ssh_cmd = [
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'ConnectTimeout=10',
        '-i', server.ssh_key_path,
        f'{server.ssh_user}@{server.host}',
        command
    ]
    
    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        logger.error(f"SSH timeout for {server.id}")
        return False, "SSH timeout"
    except Exception as e:
        logger.error(f"SSH error for {server.id}: {e}")
        return False, str(e)


def get_remote_state(server: ServerConfig, state_file_path: str) -> Optional[Dict[str, Any]]:
    """
    Получает состояние потоков с удаленного сервера
    """
    if server.is_local:
        state_file = Path(state_file_path)
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding='utf-8'))
            except Exception as e:
                logger.error(f"Error reading local state: {e}")
                return None
        return {}
    
    success, output = execute_ssh_command(server, f'cat {state_file_path}')
    if success:
        try:
            return json.loads(output)
        except Exception as e:
            logger.error(f"Error parsing remote state from {server.id}: {e}")
            return None
    return None


def start_stream_on_server(
    server: ServerConfig,
    stream_type: str,
    stream_config: Dict[str, Any],
    logs_dir: str,
) -> tuple[bool, Optional[int], Optional[str]]:
    """
    Запускает поток на указанном сервере
    """
    if stream_type == "incoming":
        return _start_incoming_stream(server, stream_config, logs_dir)
    elif stream_type == "outgoing":
        return _start_outgoing_stream(server, stream_config, logs_dir)
    else:
        return False, None, None


def cleanup_port_on_server(server: ServerConfig, port: int) -> None:
    """Убивает все ffmpeg процессы на данном порту ПЕРЕД запуском нового потока."""
    import subprocess
    
    if server.is_local:
        subprocess.run(['/opt/srt-bot/kill_port.sh', str(port)],
                     timeout=5, capture_output=True, check=False)
    else:
        ssh_key = getattr(server, 'ssh_key_path', None) or '/root/.ssh/id_rsa'
        ssh_user = getattr(server, 'ssh_user', 'root')
        ssh_base = [
            'ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=10',
            '-i', ssh_key,
            f'{ssh_user}@{server.host}'
        ]
        try:
            subprocess.run(ssh_base + [f'/opt/srt-bot/kill_port.sh {port}'],
                         timeout=10, capture_output=True, check=False)
        except Exception:
            pass


def _start_incoming_stream(
    server: ServerConfig,
    config: Dict[str, Any],
    logs_dir: str,
) -> tuple[bool, Optional[int], Optional[str]]:
    """Запускает входящий поток"""
    local_port = config["local_port_in"]
    # Пре-очистка: убить зомби на этом порту если остались
    cleanup_port_on_server(server, local_port)
    passphrase = config.get("passphrase_in", "")
    latency = config.get("latency_in", 200)
    internal_port = config.get("internal_port", local_port + 1000)
    log_path = f"{logs_dir}/in_{config['id']}.log"
    
    passphrase_part = f"&passphrase={passphrase}" if passphrase else ""
    stats_path = f"{logs_dir}/in_{config['id']}.stats"
    ffmpeg_args = (
        f'-fflags nobuffer -flags low_delay -probesize 5000000 -analyzeduration 3000000 -loglevel info -nostats '
        f'-progress "file:{stats_path}" '
        f'-i "srt://0.0.0.0:{local_port}?mode=listener{passphrase_part}&transtype=live&latency={latency}&tlpktdrop=1&nakreport=1&rcvbuf=12058624&sndbuf=12058624&oheadbw=25&maxbw=0" '
        f'-map 0 -c copy -f mpegts "udp://239.0.0.1:{internal_port}?pkt_size=1316&ttl=1&reuse=1"'
    )
    ffmpeg_cmd = f'/opt/srt-bot/ffmpeg_wrapper.sh "{log_path}" {ffmpeg_args}' 
    
    if server.is_local:
        try:
            import os
            import time
            process = subprocess.Popen(
                ffmpeg_cmd,
                shell=True,
                stdout=open(log_path, 'w'),
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )
            time.sleep(0.5)
            if process.poll() is None:
                try:
                    result = subprocess.run(
                        f'pgrep -f "ffmpeg.*{local_port}"',
                        shell=True, capture_output=True, text=True, timeout=2
                    )
                    if result.stdout.strip():
                        real_pid = int(result.stdout.strip().split()[0])
                        return True, real_pid, log_path
                except:
                    pass
                return True, process.pid, log_path
            else:
                return False, None, None
        except Exception as e:
            logger.error(f"Error starting local incoming stream: {e}")
            return False, None, None
    else:
        execute_ssh_command(server, f'mkdir -p {logs_dir}')
        
        remote_cmd = f'nohup {ffmpeg_cmd} > {log_path} 2>&1 &'
        success, _ = execute_ssh_command(server, remote_cmd)
        if success:
            import time
            time.sleep(0.5)
            pid_cmd = f'pgrep -f "ffmpeg_wrapper.*{config["local_port_in"]}" | head -1'
            success_pid, pid_output = execute_ssh_command(server, pid_cmd, timeout=3)
            if success_pid and pid_output.strip():
                try:
                    pid = int(pid_output.strip())
                    return True, pid, log_path
                except ValueError:
                    return False, None, None
        return False, None, None


def _start_outgoing_stream(
    server: ServerConfig,
    config: Dict[str, Any],
    logs_dir: str,
) -> tuple[bool, Optional[int], Optional[str]]:
    """Запускает исходящий поток"""
    local_port = config["local_port_out"]
    # Пре-очистка: убить зомби на этом порту если остались
    cleanup_port_on_server(server, local_port)
    remote_host = config["remote_host_out"]
    remote_port = config["remote_port_out"]
    passphrase = config.get("passphrase_out", "")
    latency = config.get("latency_out", 200)
    internal_port = config.get("internal_port", local_port + 1000)
    log_path = f"{logs_dir}/out_{config['id']}.log"
    
    passphrase_part = f"&passphrase={passphrase}" if passphrase else ""
    stats_path = f"{logs_dir}/out_{config['id']}.stats"
    ffmpeg_args = (
        f'-fflags nobuffer -flags low_delay -probesize 1000000 -analyzeduration 1000000 -loglevel info -nostats '
        f'-progress "file:{stats_path}" '
        f'-i "udp://239.0.0.1:{internal_port}?reuse=1" '
        f'-map 0 -c copy -f mpegts "srt://0.0.0.0:{local_port}?mode=listener{passphrase_part}&transtype=live&latency={latency}&tlpktdrop=1&nakreport=1&rcvbuf=12058624&sndbuf=12058624&oheadbw=25&maxbw=0"'
    )
    ffmpeg_cmd = f'/opt/srt-bot/ffmpeg_wrapper.sh "{log_path}" {ffmpeg_args}' 
    
    if server.is_local:
        try:
            import os
            import time
            process = subprocess.Popen(
                ffmpeg_cmd,
                shell=True,
                stdout=open(log_path, 'w'),
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )
            time.sleep(0.5)
            if process.poll() is None:
                return True, process.pid, log_path
            else:
                return False, None, None
        except Exception as e:
            print(f"Error starting local outgoing stream: {e}")
            return False, None, None
    else:
        execute_ssh_command(server, f'mkdir -p {logs_dir}')
        
        remote_cmd = f'nohup {ffmpeg_cmd} > {log_path} 2>&1 &'
        success, _ = execute_ssh_command(server, remote_cmd)
        if success:
            import time
            time.sleep(0.5)
            pid_cmd = f'pgrep -f "ffmpeg_wrapper.*{local_port}" | head -1'
            success_pid, pid_output = execute_ssh_command(server, pid_cmd, timeout=3)
            if success_pid and pid_output.strip():
                try:
                    pid = int(pid_output.strip())
                    return True, pid, log_path
                except ValueError:
                    return False, None, None
        return False, None, None


def stop_stream_on_server(
    server: ServerConfig,
    pid: int,
    port: int = None,
) -> bool:
    """
    Останавливает поток на указанном сервере.
    Для удалённых серверов использует скрипт kill_port.sh,
    который НЕ содержит 'ffmpeg' в cmdline, избегая self-match проблемы.
    """
    import subprocess
    import time
    import os
    import signal
    
    if server.is_local:
        try:
            if port:
                subprocess.run(['/opt/srt-bot/kill_port.sh', str(port)],
                             timeout=5, capture_output=True, check=False)
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return True
        except Exception as e:
            logger.error(f"Error stopping local process {pid}: {e}")
            return False
    else:
        ssh_key = getattr(server, 'ssh_key_path', None) or '/root/.ssh/id_rsa'
        ssh_user = getattr(server, 'ssh_user', 'root')
        ssh_base = [
            'ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=10',
            '-i', ssh_key,
            f'{ssh_user}@{server.host}'
        ]
        
        try:
            if port:
                cmd = f'/opt/srt-bot/kill_port.sh {port}'
                result = subprocess.run(ssh_base + [cmd], timeout=10, capture_output=True, text=True, check=False)
                logger.info(f"kill_port.sh {port} on {server.host}: rc={result.returncode}, out={result.stdout.strip()}, err={result.stderr.strip()}")
            elif pid:
                cmd = f'kill -9 {pid} 2>/dev/null; true'
                subprocess.run(ssh_base + [cmd], timeout=10, capture_output=True, check=False)
            
            return True
        except Exception as e:
            logger.error(f"Error stopping remote process pid={pid} port={port}: {e}")
            return False


def check_server_availability(server: ServerConfig) -> bool:
    """
    Проверяет доступность сервера
    """
    if server.is_local:
        return True
    
    success, _ = execute_ssh_command(server, 'echo OK', timeout=5)
    return success


def read_remote_file(server: ServerConfig, file_path: str, max_lines: int = 1000) -> str:
    """
    Читает файл с удалённого сервера через SSH.
    Для локального сервера читает напрямую.
    """
    if server.is_local:
        try:
            with open(file_path, 'r', errors='replace') as f:
                return f.read()
        except Exception:
            return ""

    ssh_key = getattr(server, 'ssh_key_path', None) or '/root/.ssh/id_rsa'
    ssh_user = getattr(server, 'ssh_user', 'root')
    ssh_base = [
        'ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=10',
        '-i', ssh_key,
        f'{ssh_user}@{server.host}'
    ]
    try:
        cmd = f'tail -{max_lines} "{file_path}" 2>/dev/null'
        result = subprocess.run(ssh_base + [cmd], timeout=10, capture_output=True, text=True, check=False)
        return result.stdout
    except Exception as e:
        logger.error(f"Error reading remote file {file_path} from {server.host}: {e}")
        return ""
