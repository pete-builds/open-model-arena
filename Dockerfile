FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/
COPY models.yaml .

# Create non-root user and own the data directory
RUN groupadd -r arena && useradd -r -g arena -d /app -s /sbin/nologin arena \
    && mkdir -p /app/data \
    && chown -R arena:arena /app/data

USER arena

EXPOSE 3694

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3694/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3694"]
