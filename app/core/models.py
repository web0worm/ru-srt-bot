from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional
import uuid


class StreamStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass
class OutgoingStream:
    id: str
    user_id: int
    local_port_out: int
    remote_host_out: str
    remote_port_out: int
    passphrase_out: Optional[str]
    latency_out: int
    status: StreamStatus
    pid: Optional[int]
    log_path: str
    start_time: Optional[float]
    stop_time: Optional[float]
    server_id: str = "spb"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @staticmethod
    def from_dict(data: dict) -> "OutgoingStream":
        return OutgoingStream(
            id=data["id"],
            user_id=data["user_id"],
            local_port_out=data["local_port_out"],
            remote_host_out=data["remote_host_out"],
            remote_port_out=data["remote_port_out"],
            passphrase_out=data.get("passphrase_out"),
            latency_out=data.get("latency_out", 120),
            status=StreamStatus(data.get("status", "stopped")),
            pid=data.get("pid"),
            log_path=data.get("log_path", ""),
            start_time=data.get("start_time"),
            stop_time=data.get("stop_time"),
            server_id=data.get("server_id", "spb"),
        )


@dataclass
class IncomingStream:
    id: str
    user_id: int
    name: str
    local_port_in: int
    internal_port: int
    remote_host_in: str
    remote_port_in: int
    passphrase_in: Optional[str]
    latency_in: int
    status: StreamStatus
    pid: Optional[int]
    log_path: str
    start_time: Optional[float]
    stop_time: Optional[float]
    outgoing_streams: List[OutgoingStream] = field(default_factory=list)
    server_id: str = "spb"
    expires_at: Optional[float] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["outgoing_streams"] = [o.to_dict() for o in self.outgoing_streams]
        return data

    @staticmethod
    def from_dict(data: dict) -> "IncomingStream":
        outgoing = [
            OutgoingStream.from_dict(o) for o in data.get("outgoing_streams", [])
        ]
        return IncomingStream(
            id=data["id"],
            user_id=data["user_id"],
            name=data["name"],
            local_port_in=data["local_port_in"],
            internal_port=data["internal_port"],
            remote_host_in=data["remote_host_in"],
            remote_port_in=data["remote_port_in"],
            passphrase_in=data.get("passphrase_in"),
            latency_in=data.get("latency_in", 120),
            status=StreamStatus(data.get("status", "stopped")),
            pid=data.get("pid"),
            log_path=data.get("log_path", ""),
            start_time=data.get("start_time"),
            stop_time=data.get("stop_time"),
            outgoing_streams=outgoing,
            server_id=data.get("server_id", "spb"),
            expires_at=data.get("expires_at"),
        )


@dataclass
class AppState:
    incoming_streams: List[IncomingStream] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"incoming_streams": [s.to_dict() for s in self.incoming_streams]}

    @staticmethod
    def from_dict(data: dict) -> "AppState":
        incoming = [
            IncomingStream.from_dict(s) for s in data.get("incoming_streams", [])
        ]
        return AppState(incoming_streams=incoming)


def create_incoming_stream(
    user_id: int,
    name: str,
    local_port_in: int,
    remote_host_in: str,
    remote_port_in: int,
    passphrase_in: str | None,
    latency: int = 120,
    server_id: str = "spb",
    expires_at: float | None = None,
) -> IncomingStream:
    stream_id = str(uuid.uuid4())
    internal_port = local_port_in + 1000
    return IncomingStream(
        id=stream_id,
        user_id=user_id,
        name=name,
        local_port_in=local_port_in,
        internal_port=internal_port,
        remote_host_in=remote_host_in,
        remote_port_in=remote_port_in,
        passphrase_in=passphrase_in,
        latency_in=latency,
        status=StreamStatus.STOPPED,
        pid=None,
        log_path="",
        start_time=None,
        stop_time=None,
        server_id=server_id,
        expires_at=expires_at,
    )


def create_outgoing_stream(
    user_id: int,
    local_port_out: int,
    remote_host_out: str,
    remote_port_out: int,
    passphrase_out: str | None,
    latency: int = 120,
    server_id: str = "spb",
) -> OutgoingStream:
    stream_id = str(uuid.uuid4())
    return OutgoingStream(
        id=stream_id,
        user_id=user_id,
        local_port_out=local_port_out,
        remote_host_out=remote_host_out,
        remote_port_out=remote_port_out,
        passphrase_out=passphrase_out,
        latency_out=latency,
        status=StreamStatus.STOPPED,
        pid=None,
        log_path="",
        start_time=None,
        stop_time=None,
        server_id=server_id,
    )
