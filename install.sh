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

# --- Optional .desktop launcher ---
echo ""
create_desktop=""
if [ -t 0 ]; then
  read -r -p "Create a desktop launcher? [y/N] " create_desktop
fi
if [[ "$create_desktop" =~ ^[Yy]$ ]]; then
  SCRIPT_DIR="$(pwd)"
  DESKTOP_FILE="$HOME/.local/share/applications/canon-import.desktop"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=Canon Import Tool
Comment=AVCHD camera import utility
Exec=bash -c 'cd "$SCRIPT_DIR" && ./venv/bin/python app.py & sleep 2 && xdg-open http://localhost:8080'
Icon=camera-video
Terminal=false
Type=Application
Categories=AudioVideo;
EOF
  chmod +x "$DESKTOP_FILE"
  echo -e "${GREEN}✓${NC} Desktop launcher created: $DESKTOP_FILE"
fi

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
