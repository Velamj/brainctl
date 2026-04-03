# ── agentmemory MCP server container ──────────────────────────────
# Build:  docker build -t agentmemory .
# Run:    docker run -v /path/to/brain.db:/data/brain.db agentmemory
# ──────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

LABEL maintainer="Terrence Schonleber"
LABEL description="agentmemory — cognitive memory system for AI agents"

# Prevent Python from writing .pyc and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps (sqlite3 is included in python:3.11-slim)
RUN apt-get update && \
    apt-get install -y --no-install-recommends sqlite3 && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 agent && \
    useradd --uid 1000 --gid agent --create-home agent

# Install agentmemory with MCP support
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir ".[mcp]"

# Data volume — mount your brain.db here
VOLUME ["/data"]
ENV BRAIN_DB=/data/brain.db

# Initialize schema if brain.db doesn't exist on first run
COPY db/init_schema.sql /app/init_schema.sql
RUN echo '#!/bin/sh\n\
if [ ! -f "$BRAIN_DB" ]; then\n\
  echo "Initializing new brain.db at $BRAIN_DB ..."\n\
  sqlite3 "$BRAIN_DB" < /app/init_schema.sql\n\
fi\n\
exec "$@"' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

USER agent

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["brainctl-mcp"]

EXPOSE 8080
