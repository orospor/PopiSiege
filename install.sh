#!/usr/bin/env bash
# PopiSiege Global Installer
# Usage: curl -sSL https://raw.githubusercontent.com/orospor/PopiSiege/main/install.sh | bash

set -e

INSTALL_DIR="/opt/popisiege"
BIN_POPI="/usr/local/bin/popisiege"
BIN_SEARCH="/usr/local/bin/search-flood"

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
python3 /opt/popisiege/popisiege.py "\$@"
EOF
chmod +x "$BIN_POPI"

cat > "$BIN_SEARCH" << EOF
#!/usr/bin/env bash
python3 /opt/popisiege/search_flood.py "\$@"
EOF
chmod +x "$BIN_SEARCH"

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
echo "  Both tools:"
echo "    --verbose        show every request"
echo "    --delay 1        pause between bursts"
echo "    --proxy-file     custom proxy list"
echo ""
