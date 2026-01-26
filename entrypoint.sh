#!/bin/bash
# F1arr Entrypoint

SCRIPT_DIR="/app"
PYTHON="/opt/venv/bin/python3"
CONFIG_DIR="${CONFIG_DIR:-/config}"
SETTINGS_FILE="${CONFIG_DIR}/settings.yml"

echo "================================================"
echo "  F1arr - F1 Automated Recording & Retrieval"
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

# Create settings.yml template if it doesn't exist
if [ ! -f "$SETTINGS_FILE" ]; then
    echo "  Creating settings.yml template..."
    cat > "$SETTINGS_FILE" << 'SETTINGS'
# F1arr Configuration
# ====================
# Edit these values to match your setup.
# Environment variables take priority over these settings.
#
# IMPORTANT: Path Configuration
# -----------------------------
# Paths in this file are CONTAINER paths, not host paths.
# They must match your Docker volume mounts.
#
# Example volume mounts (docker run):
#   -v /mnt/user/data/media:/media
#   -v /mnt/user/data/downloads:/downloads
#   -v /mnt/user/appdata/f1arr:/config
#
# Example Unraid paths:
#   Host path:       /mnt/user/data/media/Motorsport/F1
#   Container mount: /media
#   Config value:    /media/Motorsport/F1
#
#   Host path:       /mnt/user/data/downloads/complete/f1
#   Container mount: /downloads
#   Config value:    /downloads/complete/f1
#
# To find your Plex library ID:
#   1. Open Plex web interface
#   2. Go to your F1 library
#   3. Look at the URL - it contains: /library/sections/X/
#   4. X is your library_id

prowlarr:
  url: ""          # e.g., http://192.168.1.100:9696
  api_key: ""

sabnzbd:
  url: ""          # e.g., http://192.168.1.100:8080
  api_key: ""

plex:
  url: ""          # e.g., http://192.168.1.100:32400
  token: ""        # See: https://support.plex.tv/articles/204059436
  library_id: ""   # Plex library section ID (see instructions above)
  library_name: "Motorsport - F1"  # Display name of your F1 library in Plex

library:
  # Container paths - must match your volume mounts!
  f1_path: "/media/F1"              # Where F1 content is stored (inside container)
  downloads_path: "/downloads/complete/f1"  # Where SABnzbd puts completed F1 downloads

discord:
  webhook_url: ""  # Optional: Discord webhook for notifications

timezone: "Europe/London"
SETTINGS
    echo "  ⚠️  Please configure ${SETTINGS_FILE}"
fi

# Validate configuration
echo ""
echo "  Checking configuration..."
$PYTHON -c "
from utils import config
v = config.validate()
if v['valid']:
    print('  ✅ All required settings configured')
else:
    print('  ⚠️  Missing settings:', ', '.join(v['missing']))
    print('  Edit ${SETTINGS_FILE} or set environment variables')
"

echo ""
echo "  Config Dir:     ${CONFIG_DIR}"
echo "  Settings File:  ${SETTINGS_FILE}"
echo "  WebUI Port:     5678"
echo "================================================"

# Start WebUI in background
echo "Starting WebUI..."
$PYTHON "$SCRIPT_DIR/f1_webui.py" &
WEBUI_PID=$!
echo "  WebUI started (PID: $WEBUI_PID)"

sleep 2

# Run scheduler in foreground
echo "Starting Scheduler..."
exec $PYTHON "$SCRIPT_DIR/f1_scheduler.py"
