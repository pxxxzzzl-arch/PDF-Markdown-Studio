FROM node:24-slim AS frontend

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDFMD_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN python -m pip install --no-cache-dir '.[primary]'

RUN useradd --create-home --uid 10001 pdfmd && mkdir -p /data && chown -R pdfmd:pdfmd /data /app
USER pdfmd

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["uvicorn", "pdfmd.main:app", "--host", "0.0.0.0", "--port", "8000"]
