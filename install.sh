#!/usr/bin/env bash
# PopiSiege Global Installer
# Usage: curl -sSL https://raw.githubusercontent.com/orospor/PopiSiege/main/install.sh | sudo bash

set -e

INSTALL_DIR="/opt/popisiege"
BACKBONE_CREDS="$INSTALL_DIR/proxies_webshare_backbone_creds.txt"

G="\033[0;32m"; R="\033[0;31m"; Y="\033[0;33m"; W="\033[0m"; B="\033[1m"

echo ""
echo -e "${B}=============================="
echo    "  PopiSiege Installer"
echo -e "==============================${W}"
echo ""

# ── clone or update ────────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${G}[*]${W} Updating existing install..."
    git -C "$INSTALL_DIR" pull -q
else
    echo -e "${G}[*]${W} Cloning PopiSiege..."
    git clone -q https://github.com/orospor/PopiSiege.git "$INSTALL_DIR"
fi

# ── python deps ────────────────────────────────────────────────────────────────
echo -e "${G}[*]${W} Installing Python dependencies..."
apt-get install -y -q python3-pip python3-dev 2>/dev/null || true
pip3 install -r "$INSTALL_DIR/requirements.txt" -q --break-system-packages 2>/dev/null \
    || pip3 install -r "$INSTALL_DIR/requirements.txt" -q

# ── webshare creds ─────────────────────────────────────────────────────────────
echo ""
if [ -s "$BACKBONE_CREDS" ]; then
    echo -e "${G}[*]${W} Webshare creds already exist — skipping."
else
    echo -e "${Y}[?]${W} Enter Webshare rotating proxy username (blank to skip):"
    read -r WS_USER
    if [ -n "$WS_USER" ]; then
        echo -e "${Y}[?]${W} Enter Webshare proxy password:"
        read -r WS_PASS
        printf "%s\n%s\n" "$WS_USER" "$WS_PASS" > "$BACKBONE_CREDS"
        chmod 600 "$BACKBONE_CREDS"
        echo -e "${G}[✓]${W} Creds saved to $BACKBONE_CREDS"
    else
        echo -e "${Y}[!]${W} Skipped — create $BACKBONE_CREDS manually later."
    fi
fi

# ── global commands ────────────────────────────────────────────────────────────
echo -e "${G}[*]${W} Creating global commands..."

_make_cmd() {
    local bin="$1"; shift
    printf '#!/usr/bin/env bash\n%s\n' "$*" > "$bin"
    chmod +x "$bin"
}

_make_cmd /usr/local/bin/popisiege \
    "exec python3 /opt/popisiege/popisiege.py \"\$@\""

_make_cmd /usr/local/bin/popibundmara \
    "exec python3 /opt/popisiege/popisiege.py \"\$@\""

_make_cmd /usr/local/bin/search-flood \
    "exec python3 /opt/popisiege/search_flood.py \"\$@\""

_make_cmd /usr/local/bin/popiwatch \
    "exec python3 /opt/popisiege/popiwatch.py \"\$@\""

_make_cmd /usr/local/bin/dns-flood \
    "cd /opt/popisiege && exec python3 dns_flood.py \"\$@\""

_make_cmd /usr/local/bin/dns-tcp-flood \
    "cd /opt/popisiege && exec python3 dns_tcp_flood.py \"\$@\""

# ── done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}=============================="
echo    "  Done."
echo -e "==============================${W}"
echo ""
echo -e "  ${B}CF7 Worker Exhaustion:${W}"
echo    "    popibundmara --verbose"
echo    "    popibundmara --target metoo-buffalo.com"
echo    "    popibundmara --concurrency 30 --verbose"
echo ""
echo -e "  ${B}Search Flood (PHP + MySQL):${W}"
echo    "    search-flood --verbose"
echo    "    search-flood --target metoo-buffalo.com --concurrency 80"
echo ""
echo -e "  ${B}DNS Flood:${W}"
echo    "    dns-flood  --target <IP> --workers 300"
echo    "    dns-tcp-flood --target <IP> --workers 300"
echo ""
echo -e "  ${B}Defense monitor:${W}"
echo    "    popiwatch --log /var/log/nginx/access.log"
echo    "    sudo popiwatch --log /path/to/access.log --live"
echo ""
if [ ! -s "$BACKBONE_CREDS" ]; then
    echo -e "  ${Y}[!]${W} Webshare creds missing — create before running:"
    echo    "      printf 'USERNAME\\nPASSWORD\\n' > $BACKBONE_CREDS"
    echo ""
fi
