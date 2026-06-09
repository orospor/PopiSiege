#!/usr/bin/env bash
# PopiSiege Global Installer
# Usage: curl -sSL https://raw.githubusercontent.com/orospor/PopiSiege/main/install.sh | bash

set -e

INSTALL_DIR="/opt/popisiege"
BIN="/usr/local/bin/popisiege"

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
pip3 install -r "$INSTALL_DIR/requirements.txt" -q --break-system-packages

# create global launcher
echo "[*] Creating global command..."
cat > "$BIN" << EOF
#!/usr/bin/env bash
python3 /opt/popisiege/popisiege.py "\$@"
EOF
chmod +x "$BIN"

echo ""
echo "=============================="
echo "  Done. Run: popisiege"
echo "=============================="
echo ""
echo "  Commands:"
echo "    popisiege"
echo "    popisiege --target metoo-buffalo.com"
echo "    popisiege --concurrency 30"
echo "    popisiege --verbose"
echo ""
