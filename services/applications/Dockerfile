FROM python:3.12.7-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/applications.db UPLOAD_DIR=/data/uploads
WORKDIR /app
COPY services/applications/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/applications/main.py services/applications/decision.py services/applications/store.py ./
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
