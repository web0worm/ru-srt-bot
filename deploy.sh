#!/bin/bash
set -e

# ═══════════════════════════════════════════════════════
#  RU SRT BOT — One-click deploy script
#  Deploys the bot to a fresh Ubuntu 22.04/24.04 server
# ═══════════════════════════════════════════════════════

INSTALL_DIR="/opt/srt-bot"
SERVICE_NAME="srt-bot"
STATUS_SERVICE="srt-status"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Check root ──
if [ "$EUID" -ne 0 ]; then
    error "Run as root: sudo bash deploy.sh"
fi

info "Starting RU SRT BOT deployment..."

# ── Install system deps ──
info "Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg nginx > /dev/null

# ── Create install dir ──
info "Setting up ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"

# ── Determine script location (works for both git clone and curl pipe) ──
if [ -f "$(dirname "$0")/app/main.py" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    info "Deploying from local directory: ${SCRIPT_DIR}"
else
    SCRIPT_DIR="${INSTALL_DIR}"
    if [ ! -f "${INSTALL_DIR}/app/main.py" ]; then
        info "Cloning from GitHub..."
        apt-get install -y -qq git > /dev/null
        git clone https://github.com/web0worm/ru-srt-bot.git "${INSTALL_DIR}_tmp"
        cp -r "${INSTALL_DIR}_tmp/"* "${INSTALL_DIR}/"
        cp -r "${INSTALL_DIR}_tmp/".* "${INSTALL_DIR}/" 2>/dev/null || true
        rm -rf "${INSTALL_DIR}_tmp"
        SCRIPT_DIR="${INSTALL_DIR}"
    fi
fi

# ── Copy files if deploying from local clone ──
if [ "${SCRIPT_DIR}" != "${INSTALL_DIR}" ]; then
    info "Copying project files to ${INSTALL_DIR}..."
    rsync -a --exclude='venv' --exclude='.git' --exclude='__pycache__' \
        "${SCRIPT_DIR}/" "${INSTALL_DIR}/"
fi

# ── Setup .env ──
if [ ! -f "${INSTALL_DIR}/.env" ]; then
    if [ -f "${INSTALL_DIR}/.env.example" ]; then
        cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
        warn ".env created from template — EDIT IT: nano ${INSTALL_DIR}/.env"
    else
        error ".env.example not found!"
    fi
else
    info ".env already exists, keeping it"
fi

# ── Create venv & install deps ──
info "Setting up Python virtual environment..."
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip -q
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" -q
info "Python deps installed"

# ── Create data & log dirs ──
mkdir -p "${INSTALL_DIR}/data"
mkdir -p "${INSTALL_DIR}/logs"
mkdir -p "${INSTALL_DIR}/avatars"

# ── Make scripts executable ──
chmod +x "${INSTALL_DIR}/scripts/ffmpeg_wrapper.sh"
chmod +x "${INSTALL_DIR}/scripts/kill_port.sh"
chmod +x "${INSTALL_DIR}/scripts/cleanup_ffmpeg.py"
chmod +x "${INSTALL_DIR}/scripts/fetch_avatars.py"
chmod +x "${INSTALL_DIR}/scripts/check_tunnel_reminders.py"
chmod +x "${INSTALL_DIR}/scripts/send_tunnel_reminders.py"

# ── Create symlinks for scripts expected by server_manager ──
ln -sf "${INSTALL_DIR}/scripts/ffmpeg_wrapper.sh" "${INSTALL_DIR}/ffmpeg_wrapper.sh"
ln -sf "${INSTALL_DIR}/scripts/kill_port.sh" "${INSTALL_DIR}/kill_port.sh"

# ── Systemd: srt-bot ──
info "Creating systemd service: ${SERVICE_NAME}..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=SRT Telegram Bot Service
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python -m app.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# ── Systemd: srt-status ──
info "Creating systemd service: ${STATUS_SERVICE}..."
cat > "/etc/systemd/system/${STATUS_SERVICE}.service" <<EOF
[Unit]
Description=SRT status arrows HTTP server
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/status_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# ── Crontab ──
info "Setting up cron jobs..."
CRON_MARKER="# srt-bot-managed"
(crontab -l 2>/dev/null | grep -v "${CRON_MARKER}") | crontab -
(crontab -l 2>/dev/null; echo "*/5 * * * * cd ${INSTALL_DIR} && ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/scripts/cleanup_ffmpeg.py >> /var/log/ffmpeg_cleanup.log 2>&1 ${CRON_MARKER}") | crontab -
(crontab -l 2>/dev/null; echo "*/10 * * * * cd ${INSTALL_DIR} && ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/scripts/fetch_avatars.py >/dev/null 2>&1 ${CRON_MARKER}") | crontab -
(crontab -l 2>/dev/null; echo "0 */6 * * * cd ${INSTALL_DIR} && ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/scripts/check_tunnel_reminders.py | ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/scripts/send_tunnel_reminders.py ${CRON_MARKER}") | crontab -

# ── Enable & start ──
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" "${STATUS_SERVICE}"

# Only start if .env is configured
if grep -q "YOUR_BOT_TOKEN_HERE" "${INSTALL_DIR}/.env" 2>/dev/null; then
    warn "Bot NOT started — configure .env first:"
    warn "  nano ${INSTALL_DIR}/.env"
    warn "Then run:"
    warn "  systemctl start ${SERVICE_NAME}"
    warn "  systemctl start ${STATUS_SERVICE}"
else
    systemctl restart "${SERVICE_NAME}"
    systemctl restart "${STATUS_SERVICE}"
    info "Services started!"
fi

# ── Setup remote nodes ──
setup_remote_nodes() {
    if [ ! -f "${INSTALL_DIR}/.env" ]; then
        return
    fi

    SERVERS_JSON=$(grep '^SERVERS_CONFIG=' "${INSTALL_DIR}/.env" | sed "s/^SERVERS_CONFIG='//;s/'$//" | sed 's/^SERVERS_CONFIG="//' | sed 's/"$//')
    if [ -z "$SERVERS_JSON" ] || echo "$SERVERS_JSON" | grep -q "YOUR"; then
        return
    fi

    REMOTE_HOSTS=$(echo "$SERVERS_JSON" | python3 -c "
import sys, json
servers = json.load(sys.stdin)
local_ips = set()
try:
    import subprocess
    out = subprocess.check_output(['hostname', '-I'], text=True).strip()
    local_ips = set(out.split())
except: pass
for s in servers:
    h = s.get('host','')
    if h and h not in local_ips and h != '127.0.0.1':
        u = s.get('ssh_user','root')
        k = s.get('ssh_key_path','/root/.ssh/id_rsa')
        print(f\"{u}@{h}|{k}|{s.get('name','')}\")
" 2>/dev/null || true)

    if [ -z "$REMOTE_HOSTS" ]; then
        return
    fi

    info "Setting up remote nodes..."
    DEPLOY_NODE_URL="https://raw.githubusercontent.com/web0worm/ru-srt-bot/main/deploy-node.sh"

    while IFS= read -r line; do
        SSH_TARGET=$(echo "$line" | cut -d'|' -f1)
        SSH_KEY=$(echo "$line" | cut -d'|' -f2)
        SRV_NAME=$(echo "$line" | cut -d'|' -f3)

        if [ ! -f "$SSH_KEY" ]; then
            warn "SSH key $SSH_KEY not found, skipping ${SRV_NAME} (${SSH_TARGET})"
            continue
        fi

        info "Deploying node: ${SRV_NAME} (${SSH_TARGET})..."
        ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -i "$SSH_KEY" "$SSH_TARGET" \
            "bash <(curl -sL ${DEPLOY_NODE_URL})" 2>&1 | while read -r l; do echo "  [${SRV_NAME}] $l"; done

        if [ $? -eq 0 ]; then
            info "${SRV_NAME} node ready"
        else
            warn "Failed to setup ${SRV_NAME} node"
        fi
    done <<< "$REMOTE_HOSTS"
}

# Try to setup remote nodes if .env is configured
if ! grep -q "YOUR_BOT_TOKEN_HERE" "${INSTALL_DIR}/.env" 2>/dev/null; then
    setup_remote_nodes
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}  RU SRT BOT deployed successfully!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo ""
echo "  Install dir:  ${INSTALL_DIR}"
echo "  Config:       ${INSTALL_DIR}/.env"
echo "  Logs:         journalctl -u ${SERVICE_NAME} -f"
echo "  Status:       systemctl status ${SERVICE_NAME}"
echo ""
echo "  Quick commands:"
echo "    systemctl restart ${SERVICE_NAME}   # Restart bot"
echo "    systemctl stop ${SERVICE_NAME}      # Stop bot"
echo "    nano ${INSTALL_DIR}/.env            # Edit config"
echo ""
echo "  Remote nodes (run on each remote server):"
echo "    bash <(curl -sL https://raw.githubusercontent.com/web0worm/ru-srt-bot/main/deploy-node.sh)"
echo ""
