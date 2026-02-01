#!/bin/bash
set -e

BIN_PATH="$HOME/.local/bin/fastkill"
DESKTOP_PATH="$HOME/.local/share/applications/fastkill.desktop"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "$1" == "--remove" ]]; then
    rm -f "$BIN_PATH" "$DESKTOP_PATH"
    echo "FastKill removed"
    exit 0
fi

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
cp "$SCRIPT_DIR/fastkill.py" "$BIN_PATH"
chmod +x "$BIN_PATH"

cat > "$DESKTOP_PATH" << EOF
[Desktop Entry]
Name=FastKill
Comment=Kill runaway processes
Exec=$BIN_PATH
Icon=system-run
Type=Application
Categories=System;Utility;
EOF

echo "FastKill installed - available in app menu and as 'fastkill' command"

if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    echo ""
    echo "Note: To run 'fastkill' from terminal, add to your shell config:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "App menu entry works without this."
fi
