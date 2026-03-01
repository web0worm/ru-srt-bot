import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Settings:
    bot_token: str
    server_public_ip: str
    incoming_port_start: int
    incoming_port_end: int
    outgoing_port_start: int
    outgoing_port_end: int
    state_file: str
    logs_dir: str
    max_incoming_streams: int
    admin_user_id: int | None = None

    # Backward-compatible свойства для старого кода (storage и пр.)
    @property
    def incoming_port_range(self) -> tuple[int, int]:
        return (self.incoming_port_start, self.incoming_port_end)

    @property
    def outgoing_port_range(self) -> tuple[int, int]:
        return (self.outgoing_port_start, self.outgoing_port_end)


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    server_public_ip = os.getenv("SERVER_PUBLIC_IP", "127.0.0.1")

    incoming_range = os.getenv("INCOMING_PORT_RANGE", "5000-5020")
    outgoing_range = os.getenv("OUTGOING_PORT_RANGE", "7000-7100")

    in_start_str, in_end_str = incoming_range.split("-")
    out_start_str, out_end_str = outgoing_range.split("-")

    incoming_port_start = int(in_start_str)
    incoming_port_end = int(in_end_str)
    outgoing_port_start = int(out_start_str)
    outgoing_port_end = int(out_end_str)

    state_file = os.getenv("STATE_FILE", "data/state.json")
    logs_dir = os.getenv("LOGS_DIR", "logs")
    max_incoming_streams = int(os.getenv("MAX_INCOMING_STREAMS", "20"))

    admin_user_id_str = os.getenv("ADMIN_USER_ID")
    admin_user_id = int(admin_user_id_str) if admin_user_id_str else None

    # Гарантируем, что папки существуют
    state_dir = os.path.dirname(state_file)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)

    return Settings(
        bot_token=bot_token,
        server_public_ip=server_public_ip,
        incoming_port_start=incoming_port_start,
        incoming_port_end=incoming_port_end,
        outgoing_port_start=outgoing_port_start,
        outgoing_port_end=outgoing_port_end,
        state_file=state_file,
        logs_dir=logs_dir,
        max_incoming_streams=max_incoming_streams,
        admin_user_id=admin_user_id,
    )
