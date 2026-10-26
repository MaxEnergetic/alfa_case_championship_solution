FROM python:3.12.7-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements-scoring.txt ./
RUN pip install --no-cache-dir -r requirements-scoring.txt
COPY backend/fns_parser fns_parser
EXPOSE 8000
CMD ["uvicorn", "fns_parser.api:app", "--host", "0.0.0.0", "--port", "8000"]
