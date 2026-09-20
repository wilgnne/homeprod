FROM node:22-bookworm-slim

# The official CLI also starts the 9Router HTTP server.
RUN npm install --global 9router@latest \
    && npm cache clean --force \
    && mkdir -p /data \
    && chown -R node:node /data

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=20128 \
    DATA_DIR=/data

USER node
WORKDIR /data

EXPOSE 20128

# `--tray` selects the CLI's non-interactive server mode.  The standard mode
# opens a terminal menu and exits when Docker provides no TTY.
CMD ["9router", "--tray", "--no-browser", "--skip-update", "--host", "0.0.0.0"]
