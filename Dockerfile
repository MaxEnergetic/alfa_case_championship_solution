FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ALFA_FRONTEND_DIR=/app/frontend \
    FNS_DOWNLOAD_DIR=/app/data/extracts \
    CREDIT_DB_PATH=/app/data/credit.sqlite3 \
    FNS_CAPTCHA_DB=/app/data/captcha.sqlite3 \
    SCORING_CACHE_DIR=/app/data/scoring_cache

WORKDIR /app

COPY backend/requirements.txt backend/requirements-scoring.txt /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/requirements-scoring.txt

COPY backend/ /app/backend/
RUN pip install --no-cache-dir --no-deps -e /app/backend

COPY frontend/ /app/frontend/
RUN mkdir -p /app/data/extracts /app/data/scoring_cache

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "fns_parser.api:app", "--host", "0.0.0.0", "--port", "8000"]
