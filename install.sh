#!/usr/bin/env bash
# Canon Import Tool — installer (Option A)
# Sets up a local virtualenv and installs dependencies.
# Usage: bash install.sh

set -e
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo ""
echo "Canon Import Tool — Setup"
echo "========================="
echo ""

# --- Check Python 3.8+ ---
if ! command -v python3 &>/dev/null; then
  echo -e "${RED}ERROR: python3 not found. Install Python 3.8+ and re-run.${NC}"
  exit 1
fi
PY_VER=$(python3 -c 'import sys; print(sys.version_info[:2])')
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)'; then
  echo -e "${GREEN}✓${NC} Python 3: $(python3 --version)"
else
  echo -e "${RED}ERROR: Python 3.8+ required (found $(python3 --version)).${NC}"
  exit 1
fi

# --- Check ffprobe ---
if command -v ffprobe &>/dev/null; then
  echo -e "${GREEN}✓${NC} ffprobe: $(ffprobe -version 2>&1 | head -1)"
else
  echo -e "${RED}ERROR: ffprobe not found.${NC}"
  echo "  Install with:  sudo apt install ffmpeg"
  exit 1
fi

# --- Create virtualenv ---
if [ ! -d "venv" ]; then
  echo ""
  echo "Creating virtualenv..."
  python3 -m venv venv
  echo -e "${GREEN}✓${NC} virtualenv created at ./venv"
else
  echo -e "${GREEN}✓${NC} virtualenv already exists"
fi

# --- Install dependencies ---
echo ""
echo "Installing dependencies..."
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
echo -e "${GREEN}✓${NC} flask installed"

# --- Copy example settings if needed ---
if [ ! -f "settings.json" ]; then
  cp settings.example.json settings.json
  echo -e "${YELLOW}!${NC} Created settings.json from example — edit it to set your archive path."
fi

# --- Optional launchers + auto-start service ---
# launch.sh: health-checks the server, starts it via the venv if needed,
# then opens the browser. Used by all launchers below.
if [ ! -x "launch.sh" ]; then
  echo ""
  echo "launch.sh not found — creating it..."
  cat > launch.sh <<'EOF'
#!/bin/bash
# Start the Canon Import Tool server (if not already running) and open the browser.
# Safe to run from terminal, app launcher, or cron — returns immediately.
cd "$(dirname "$0")"

BASE_URL="http://localhost:8080"
LOGFILE="$HOME/.cache/camcorder-import.log"

up() { curl -s -o /dev/null --max-time 1 "$BASE_URL"; }

if ! up; then
    nohup setsid ./venv/bin/python app.py > "$LOGFILE" 2>&1 &
    disown
    # Wait up to 10s for the server to come up
    for _ in $(seq 1 20); do
        up && break
        sleep 0.5
    done
    if ! up; then
        echo "Failed to start server — see $LOGFILE" >&2
        exit 1
    fi
fi

nohup xdg-open "$BASE_URL" >/dev/null 2>&1 &
disown
EOF
  chmod +x launch.sh
  echo -e "${GREEN}✓${NC} launch.sh created"
fi

echo ""
read -r -p "Install menu launcher, 'camcorder' command, and boot-time service? [Y/n] " want_launchers
if [ -t 0 ] || [ -z "$want_launchers" ]; then want_launchers="y"; fi
if [[ "$want_launchers" =~ ^[Nn]$ ]]; then
  echo -e "${YELLOW}!${NC} Skipping launcher/service install."
else
  SCRIPT_DIR="$(pwd)"

  # 1. Desktop launcher (app menu entry) — points at launch.sh
  DESKTOP_FILE="$HOME/.local/share/applications/canon-import.desktop"
  mkdir -p "$HOME/.local/share/applications"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=Canon Import Tool
Comment=Import AVCHD footage, browse archive, upload to Glacier
Exec=$SCRIPT_DIR/launch.sh
Icon=camera-video
Terminal=false
Type=Application
Categories=AudioVideo;
EOF
  chmod +x "$DESKTOP_FILE"
  echo -e "${GREEN}✓${NC} App menu launcher: $DESKTOP_FILE"

  # 2. Command-line shortcut
  mkdir -p "$HOME/.local/bin"
  ln -sf "$SCRIPT_DIR/launch.sh" "$HOME/.local/bin/camcorder"
  echo -e "${GREEN}✓${NC} Terminal command: ~/.local/bin/camcorder"

  # 3. User-level systemd service — starts on boot, auto-restarts on crash
  SERVICE_DIR="$HOME/.config/systemd/user"
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_DIR/canon-import.service" <<EOF
[Unit]
Description=Canon Import Tool (Flask web UI on port 8080)
After=default.target

[Service]
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/venv/bin/python app.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now canon-import.service
  echo -e "${GREEN}✓${NC} User service installed and running (systemctl --user status canon-import)"

  # Linger = user services start at boot even without a login session
  loginctl enable-linger "$USER" 2>/dev/null || true
  if [ "$(loginctl show-user "$USER" -p Linger 2>/dev/null | cut -d= -f2)" = "yes" ]; then
    echo -e "${GREEN}✓${NC} Boot auto-start enabled (linger=yes)"
  else
    echo -e "${YELLOW}!${NC} Could not enable linger — run: loginctl enable-linger $USER"
  fi
fi

echo ""
echo "Stop a running manual copy before switching to the service:"
echo "  pkill -f 'venv/bin/python app.py'"
echo ""

echo ""
echo -e "${GREEN}Setup complete.${NC}"
echo ""
echo "To start the app:"
echo "  cd $(pwd)"
echo "  ./venv/bin/python app.py"
echo ""
echo "Then open:  http://localhost:8080"
echo ""
echo "To run the CLI directly:"
echo "  ./venv/bin/python camcorder_import.py --help"
echo ""
