#!/bin/bash
set -e

# ═══════════════════════════════════════════════════════
#  RU SRT BOT — Remote node deploy
#  Run on REMOTE servers (e.g. Moscow)
#  Installs only ffmpeg + helper scripts (no bot)
# ═══════════════════════════════════════════════════════

INSTALL_DIR="/opt/srt-bot"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

if [ "$EUID" -ne 0 ]; then
    error "Run as root: sudo bash deploy-node.sh"
fi

info "Setting up SRT remote node..."

# ── Install ffmpeg ──
info "Installing ffmpeg..."
apt-get update -qq
apt-get install -y -qq ffmpeg > /dev/null

# ── Create dirs ──
mkdir -p "${INSTALL_DIR}/scripts"
mkdir -p "${INSTALL_DIR}/logs"
mkdir -p "${INSTALL_DIR}/data"

# ── Download scripts ──
REPO_RAW="https://raw.githubusercontent.com/web0worm/ru-srt-bot/main"

info "Downloading scripts..."
curl -sL "${REPO_RAW}/scripts/ffmpeg_wrapper.sh" -o "${INSTALL_DIR}/scripts/ffmpeg_wrapper.sh"
curl -sL "${REPO_RAW}/scripts/kill_port.sh"      -o "${INSTALL_DIR}/scripts/kill_port.sh"

chmod +x "${INSTALL_DIR}/scripts/ffmpeg_wrapper.sh"
chmod +x "${INSTALL_DIR}/scripts/kill_port.sh"

# ── Symlinks (server_manager expects them in /opt/srt-bot/) ──
ln -sf "${INSTALL_DIR}/scripts/ffmpeg_wrapper.sh" "${INSTALL_DIR}/ffmpeg_wrapper.sh"
ln -sf "${INSTALL_DIR}/scripts/kill_port.sh"      "${INSTALL_DIR}/kill_port.sh"

# ── Verify ffmpeg ──
if command -v ffmpeg &>/dev/null; then
    FFMPEG_VER=$(ffmpeg -version 2>&1 | head -1)
    info "ffmpeg: ${FFMPEG_VER}"
else
    error "ffmpeg not found after install!"
fi

# ── Check SRT support ──
if ffmpeg -protocols 2>&1 | grep -q srt; then
    info "SRT protocol: supported"
else
    warn "SRT protocol NOT found in ffmpeg! Streams may not work."
    warn "You may need to install ffmpeg with SRT support."
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}  Remote node ready!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo ""
echo "  Install dir: ${INSTALL_DIR}"
echo ""
echo "  Next steps on the MAIN server (where bot runs):"
echo "    1. Generate SSH key:  ssh-keygen -t rsa -b 4096 -N ''"
echo "    2. Copy to this node: ssh-copy-id root@$(hostname -I | awk '{print $1}')"
echo "    3. Add this server to SERVERS_CONFIG in .env"
echo ""
