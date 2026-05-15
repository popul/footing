FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY static/ ./static/

RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown 1000:1000 /data

USER 1000
EXPOSE 8080

ENV STATIC_DIR=/app/static \
    DATA_DIR=/data \
    PORT=8080

CMD ["python", "/app/server.py"]
