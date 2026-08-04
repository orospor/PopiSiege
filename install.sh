#!/usr/bin/env bash
# PopiSiege Global Installer
# Usage: curl -sSL https://raw.githubusercontent.com/orospor/PopiSiege/main/install.sh | sudo bash

set -e

INSTALL_DIR="/opt/popisiege"
BIN_POPI="/usr/local/bin/popisiege"
BIN_SEARCH="/usr/local/bin/search-flood"
BIN_GET="/usr/local/bin/get-burst"
BIN_VPS="/usr/local/bin/vps-burst"
BIN_WATCH="/usr/local/bin/popiwatch"
BIN_CF="/usr/local/bin/popicf"
BACKBONE_CREDS="$INSTALL_DIR/proxies_webshare_backbone_creds.txt"

echo ""
echo "=============================="
echo "  PopiSiege Installer"
echo "=============================="
echo ""

# clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "[*] Updating existing install..."
    git -C "$INSTALL_DIR" pull
else
    echo "[*] Cloning PopiSiege..."
    git clone https://github.com/orospor/PopiSiege.git "$INSTALL_DIR"
fi

# install python deps
echo "[*] Installing Python dependencies..."
pip3 install -r "$INSTALL_DIR/requirements.txt" -q --break-system-packages 2>/dev/null \
    || pip3 install -r "$INSTALL_DIR/requirements.txt" -q

# create global launchers
echo "[*] Creating global commands..."

cat > "$BIN_POPI" << EOF
#!/usr/bin/env bash
sudo PROXY_USER="\$PROXY_USER" PROXY_PASS="\$PROXY_PASS" python3 /opt/popisiege/popisiege.py "\$@"
EOF
chmod +x "$BIN_POPI"

cat > "$BIN_SEARCH" << EOF
#!/usr/bin/env bash
python3 /opt/popisiege/search_flood.py "\$@"
EOF
chmod +x "$BIN_SEARCH"

cat > "$BIN_GET" << EOF
#!/usr/bin/env bash
sudo PROXY_USER="\$PROXY_USER" PROXY_PASS="\$PROXY_PASS" python3 /opt/popisiege/get_burst.py "\$@"
EOF
chmod +x "$BIN_GET"

cat > "$BIN_VPS" << EOF
#!/usr/bin/env bash
python3 /opt/popisiege/vps_burst.py "\$@"
EOF
chmod +x "$BIN_VPS"

cat > "$BIN_WATCH" << EOF
#!/usr/bin/env bash
python3 /opt/popisiege/popiwatch.py "\$@"
EOF
chmod +x "$BIN_WATCH"

# popicf — one-word launcher for the Webshare backbone-connection setup.
# The creds file is deliberately NOT embedded in this script (it would land
# in git otherwise) — it must already exist at $BACKBONE_CREDS, created once
# per machine from your Webshare backbone proxy list export.
cat > "$BIN_CF" << EOF
#!/usr/bin/env bash
CREDS="$BACKBONE_CREDS"
if [ ! -s "\$CREDS" ]; then
    echo "Missing or empty \$CREDS"
    echo "Create it once with your Webshare backbone list (p.webshare.io:80:user-N:pass per line), then rerun."
    exit 1
fi
sudo python3 /opt/popisiege/popisiege.py --concurrency 19 --verbose --proxy-file "\$CREDS" "\$@"
EOF
chmod +x "$BIN_CF"

echo ""
echo "=============================="
echo "  Done."
echo "=============================="
echo ""
echo "  CF7 Worker Exhaustion:"
echo "    popisiege"
echo "    popisiege --target metoo-buffalo.com"
echo "    popisiege --concurrency 30"
echo ""
echo "  Search Flood (PHP + MySQL):"
echo "    search-flood"
echo "    search-flood --target metoo-buffalo.com"
echo "    search-flood --concurrency 80"
echo ""
echo "  REST API POST Burst:"
echo "    vps-burst"
echo ""
echo "  REST API GET Flood:"
echo "    get-burst"
echo ""
echo "  Origin-level defense (Cloudflare-independent, dry-run by default):"
echo "    popiwatch --log /path/to/access.log"
echo "    sudo popiwatch --log /path/to/access.log --live    # actually blocks via iptables"
echo ""
echo "  Webshare backbone connection, one-word launcher:"
echo "    popicf"
echo "    (needs $BACKBONE_CREDS to exist first — create it once from your"
echo "     Webshare backbone proxy list export, gitignored, never committed)"
echo ""
echo "  All tools:"
echo "    --verbose        show every request"
echo "    --delay 1        pause between bursts"
echo "    --proxy-file     custom proxy list"
echo ""
echo "  Authenticated proxies (Webshare, etc.):"
echo "    export PROXY_USER=youruser"
echo "    export PROXY_PASS=yourpass"
echo "    popisiege --proxy-file proxies_webshare.txt"
echo "    (proxies_webshare.txt holds host:port only — creds come from env vars,"
echo "     never store them in a file that gets committed to git)"
echo ""
