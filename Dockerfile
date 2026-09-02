# One image for the whole system. Each service (5 MCP servers + the host) runs
# the SAME image with a different command — defined in docker-compose.yml. In dev,
# src/ is bind-mounted over /app/src so edits are live (just restart the service).

FROM python:3.12-slim

# Faster, quieter Python in containers
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# tzdata so TZ=Australia/Sydney resolves (slim images ship UTC-only). 
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install deps first (layer caching — deps rarely change vs. code)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the code (in dev this is shadowed by the bind-mount; in prod it's the code)
COPY . .

# MCP servers must listen on all interfaces so sibling containers can reach them.
ENV MCP_BIND_HOST=0.0.0.0

# Default command is overridden per-service in docker-compose.yml
CMD ["python", "-m", "src.client.app"]