# Сервис скоринга: модель и адаптер признаков берутся из backend/fns_parser без копирования кода.
FROM python:3.12.7-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend:/app/scoring
WORKDIR /app
COPY backend/requirements.txt backend/requirements-scoring.txt backend/
RUN pip install --no-cache-dir -r backend/requirements-scoring.txt
COPY backend/fns_parser backend/fns_parser
COPY services/scoring/app.py scoring/app.py
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=10 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app:app", "--app-dir", "/app/scoring", "--host", "0.0.0.0", "--port", "8000"]
