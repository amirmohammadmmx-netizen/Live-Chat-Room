FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REQUIRE_SECRET_KEY=1 \
    HOST=0.0.0.0 \
    DATABASE_PATH=/data/chat.sqlite3 \
    UPLOAD_FOLDER=/data/uploads

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 chat \
    && mkdir -p /data/uploads \
    && chown -R chat:chat /app /data

COPY --chown=chat:chat . .
USER chat
VOLUME ["/data"]
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:5000/health', timeout=2)"

CMD ["gunicorn", "--worker-class", "gthread", "--workers", "1", "--threads", "100", "--bind", "0.0.0.0:5000", "main:app"]
