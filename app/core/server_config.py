#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Конфигурация серверов для мультисерверной работы
"""
import json
import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ServerConfig:
    """Конфигурация одного сервера"""
    id: str  # "spb" или "msk"
    name: str  # Отображаемое имя
    host: str  # IP адрес или hostname
    domain: str  # Домен для URL (ff264.org или msk.ff264.org)
    ssh_user: str  # Пользователь для SSH
    ssh_key_path: str  # Путь к SSH ключу
    incoming_port_start: int
    incoming_port_end: int
    outgoing_port_start: int
    outgoing_port_end: int
    is_local: bool = False  # True если это локальный сервер (где работает бот)


# ═══ Кэш конфигурации ═══
_cached_servers: Optional[List[ServerConfig]] = None


def get_servers_config() -> List[ServerConfig]:
    """
    Загружает конфигурацию серверов из .env или возвращает дефолтную.
    Результат кэшируется — .env читается только один раз.
    """
    global _cached_servers
    if _cached_servers is not None:
        return _cached_servers

    from dotenv import load_dotenv
    load_dotenv()

    servers_json = os.getenv("SERVERS_CONFIG")

    if servers_json:
        try:
            servers_data = json.loads(servers_json)
            servers = []
            for s in servers_data:
                is_local = s.get("id") == "spb"
                servers.append(ServerConfig(
                    id=s["id"],
                    name=s["name"],
                    host=s["host"],
                    domain=s["domain"],
                    ssh_user=s.get("ssh_user", "root"),
                    ssh_key_path=s.get("ssh_key_path", "/root/.ssh/id_rsa"),
                    incoming_port_start=s["incoming_port_start"],
                    incoming_port_end=s["incoming_port_end"],
                    outgoing_port_start=s["outgoing_port_start"],
                    outgoing_port_end=s["outgoing_port_end"],
                    is_local=is_local,
                ))
            _cached_servers = servers
            return servers
        except Exception as e:
            print(f"Error parsing SERVERS_CONFIG: {e}")

    _cached_servers = get_default_servers()
    return _cached_servers


def get_default_servers() -> List[ServerConfig]:
    """Возвращает дефолтную конфигурацию серверов"""
    return [
        ServerConfig(
            id="spb",
            name="Санкт-Петербург",
            host="83.222.17.46",
            domain="ff264.org",
            ssh_user="root",
            ssh_key_path="/root/.ssh/id_rsa",
            incoming_port_start=5000,
            incoming_port_end=5020,
            outgoing_port_start=7000,
            outgoing_port_end=7100,
            is_local=True,
        ),
        ServerConfig(
            id="msk",
            name="Москва",
            host="194.156.117.119",
            domain="msk.ff264.org",
            ssh_user="root",
            ssh_key_path="/root/.ssh/id_rsa",
            incoming_port_start=4000,
            incoming_port_end=4020,
            outgoing_port_start=6000,
            outgoing_port_end=6100,
            is_local=False,
        ),
    ]


def get_server_by_id(server_id: str) -> Optional[ServerConfig]:
    """Возвращает конфигурацию сервера по ID"""
    servers = get_servers_config()
    for s in servers:
        if s.id == server_id:
            return s
    return None


def get_server_id_by_port(port: int) -> str:
    """
    Определяет server_id по номеру порта.
    Единая точка маппинга порт → сервер вместо хардкода в 5+ местах.
    """
    servers = get_servers_config()
    for s in servers:
        if s.incoming_port_start <= port <= s.incoming_port_end:
            return s.id
        if s.outgoing_port_start <= port <= s.outgoing_port_end:
            return s.id
    return "spb"  # дефолт
