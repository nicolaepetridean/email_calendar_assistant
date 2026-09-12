FROM python:3.11-slim

# Non-root user — reduces blast radius of any RCE via email content.
RUN addgroup --system --gid 1001 agent \
 && adduser  --system --uid 1001 --gid 1001 --no-create-home agent \
 && mkdir -p /data /credentials \
 && chown 1001:1001 /data /credentials

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/

# Verify the DB is reachable — catches a hung or misconfigured agent quickly.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import sqlite3, sys; sqlite3.connect('/data/agent.db').execute('SELECT 1'); sys.exit(0)" || exit 1

USER 1001

VOLUME ["/credentials", "/data"]
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "agent.main"]
