FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/dougalbob/f1arr"
LABEL org.opencontainers.image.description="F1 Automated Recording & Retrieval - Grabs and organizes F1 sessions for Plex"
LABEL org.opencontainers.image.version="1.0.0"

# Set timezone
ENV TZ=Europe/London
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    libjpeg62-turbo \
    libpng16-16 \
    zlib1g \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    requests \
    Pillow \
    python-dotenv \
    plexapi \
    python-dateutil \
    flask
    pyyaml
    
# Create app directory structure
WORKDIR /app
RUN mkdir -p /app/config_defaults /app/assets /app/utils

# Copy utils module
COPY utils/ /app/utils/

# Copy scripts
COPY f1_common.py /app/
COPY f1_discord.py /app/
COPY f1_grabber.py /app/
COPY f1_overlay_utility.py /app/
COPY f1_plex_tagger.py /app/
COPY f1_scheduler.py /app/
COPY f1_sort.py /app/
COPY f1_watcher.py /app/
COPY f1_webui.py /app/
COPY entrypoint.sh /app/

# Copy assets (fonts, fanart, posters, icon)
COPY assets/ /app/assets/

# Copy default config (calendars) to defaults folder
COPY config/f1_*.json /app/config_defaults/

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Environment defaults (user SHOULD override these)
ENV PYTHONUNBUFFERED=1
ENV F1_LIBRARY_PATH=/media/F1
ENV F1_DOWNLOADS_PATH=/downloads/complete/f1
ENV CONFIG_DIR=/config

# Expose WebUI port
EXPOSE 5678

# Health check
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5678/ || exit 1

# Run entrypoint
CMD ["/bin/bash", "/app/entrypoint.sh"]
