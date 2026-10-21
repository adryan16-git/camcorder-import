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
