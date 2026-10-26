# syntax=docker/dockerfile:1
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.txt requirements-scoring.txt requirements-docker.txt ./
RUN python -m pip install --no-cache-dir -r requirements-docker.txt setuptools==84.0.0
COPY pyproject.toml ./
COPY fns_parser ./fns_parser
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation . \
    && python -m pip check

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CREDIT_DB_PATH=/app/data/credit.sqlite3 \
    FNS_CAPTCHA_DB=/app/data/captcha.sqlite3 \
    FNS_DOWNLOAD_DIR=/app/data/extracts \
    SCORING_CACHE_DIR=/app/data/scoring_cache
COPY --from=builder /opt/venv /opt/venv
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data /app/output \
    && chown -R app:app /app
WORKDIR /app
COPY --chown=app:app THIRD_PARTY_NOTICES /app/THIRD_PARTY_NOTICES
USER 10001:10001
# Validate packaged model hashes and versions at build time without external requests.
RUN python -c "from fns_parser.scoring.service import load_model; load_model()"
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3); assert json.load(r)['status']=='ok'"]
CMD ["python", "-m", "uvicorn", "fns_parser.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
