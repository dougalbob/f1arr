#!/bin/bash
# F1arr Entrypoint

SCRIPT_DIR="/app"
PYTHON="/opt/venv/bin/python3"
CONFIG_DIR="${CONFIG_DIR:-/config}"

echo "================================================"
echo "  F1arr - F1 Automated Recording & Retrieval"
echo "================================================"
echo "  Library Path:   ${F1_LIBRARY_PATH:-/media/F1}"
echo "  Downloads Path: ${F1_DOWNLOADS_PATH:-/downloads/complete/f1}"
echo "  Config Dir:     ${CONFIG_DIR}"
echo "  WebUI Port:     5678"
echo "================================================"

# Ensure config directory exists
mkdir -p "${CONFIG_DIR}"

# Copy calendar files if they don't exist (first run)
for calendar in /app/config_defaults/f1_*.json; do
    if [ -f "$calendar" ]; then
        filename=$(basename "$calendar")
        if [ ! -f "${CONFIG_DIR}/${filename}" ]; then
            echo "  Copying ${filename} to config..."
            cp "$calendar" "${CONFIG_DIR}/"
        fi
    fi
done

# Start WebUI in background
echo "Starting WebUI..."
$PYTHON "$SCRIPT_DIR/f1_webui.py" &
WEBUI_PID=$!
echo "  WebUI started (PID: $WEBUI_PID)"

sleep 2

# Run scheduler in foreground
echo "Starting Scheduler..."
exec $PYTHON "$SCRIPT_DIR/f1_scheduler.py"
