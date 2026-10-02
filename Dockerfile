# job-radar in Docker: one image for the three services (search scheduler, Telegram bot, portal).
# Build and run with docker compose (see docker-compose.yml and the README, section "Docker").

# --- build the portal UI --------------------------------------------------------------------------------
FROM node:22-bookworm-slim AS portal-build
WORKDIR /app/portal
COPY portal/package.json portal/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY portal/ ./
RUN npm run build

# --- runtime: Node 22 (portal) + Python 3.11 (job-radar) --------------------------------------------------
FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /app/.venv && /app/.venv/bin/pip install --no-cache-dir -r requirements.txt

COPY job_radar/ job_radar/
COPY config.example.yaml profile.example.md .env.example ./
COPY scripts/docker/ scripts/docker/
COPY --from=portal-build /app/portal /app/portal

# Your files are mounted at /data (see docker-compose.yml). These folders are created here so the Docker volumes
# that land on them are writable by the unprivileged "node" user.
RUN mkdir -p /data/data /data/logs /policy && chown -R node:node /data /policy

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    JOB_RADAR_HOME=/data \
    JOB_RADAR_LOG_DIR=/data/logs \
    JOB_RADAR_RUNTIME=docker

USER node
CMD ["python", "-m", "job_radar", "schedule"]
