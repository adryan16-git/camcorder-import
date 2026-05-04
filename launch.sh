#!/bin/bash
# Start the Canon Import Tool server (if not already running) and open the browser.
cd "$(dirname "$0")"

if ! pgrep -f "python3 app.py" > /dev/null 2>&1; then
    python3 app.py &
    sleep 1
fi

xdg-open http://localhost:8080
