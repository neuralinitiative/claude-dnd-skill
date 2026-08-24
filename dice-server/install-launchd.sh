#!/usr/bin/env bash
# Install the dice server as a launchd LaunchAgent on macOS so it auto-starts
# at login and restarts on crash. Idempotent — safe to re-run after upgrades.
#
# Usage:   ./install-launchd.sh
# Uninstall: launchctl unload ~/Library/LaunchAgents/com.dnd-skill.dice-server.plist
#            rm ~/Library/LaunchAgents/com.dnd-skill.dice-server.plist

set -euo pipefail

SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$SERVER_DIR/com.dnd-skill.dice-server.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.dnd-skill.dice-server.plist"

if [ ! -f "$TEMPLATE" ]; then
  echo "error: template not found at $TEMPLATE" >&2
  exit 1
fi

if ! python3 -c "import flask" >/dev/null 2>&1; then
  echo "Flask not installed. Run: pip3 install flask" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

# Marker directory — dice.py uses this to auto-enable physical-roll routing
# without requiring the user to set DND_DICE_PHYSICAL=1 in their shell.
mkdir -p "$HOME/.dnd-dice"
cat > "$HOME/.dnd-dice/README.md" <<'MARKER_README'
# .dnd-dice marker

This directory's existence tells `dice.py` to route rolls through the
local dice server when it's reachable. Created by the dice-server
install-launchd.sh script.

To disable the physical-roll routing without uninstalling the server,
delete this directory. To re-enable, run install-launchd.sh again or
just recreate the directory: `mkdir ~/.dnd-dice`.
MARKER_README

# Substitute placeholders
sed -e "s|{{HOME}}|$HOME|g" -e "s|{{SERVER_DIR}}|$SERVER_DIR|g" "$TEMPLATE" > "$TARGET"

# Reload if already loaded
launchctl unload "$TARGET" 2>/dev/null || true
launchctl load "$TARGET"

sleep 1

if curl -sf http://localhost:7777/health >/dev/null; then
  IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "127.0.0.1")
  echo "✓ dice server installed and running"
  echo "   local:   http://localhost:7777"
  echo "   network: http://$IP:7777/?player=<your-name>"
  echo "   logs:    $SERVER_DIR/server.log"
else
  echo "✗ dice server failed to start. Check $SERVER_DIR/server.log" >&2
  exit 1
fi
