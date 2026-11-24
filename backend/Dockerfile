# Backend image: FastAPI + uvicorn, runs as a non-root user.
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Optional: LibreOffice for the highest-fidelity legacy .doc/.rtf conversion (adds ~400 MB).
# The built-in pure-Python .doc reader works without it.
#   docker compose build --build-arg INSTALL_LIBREOFFICE=true api
ARG INSTALL_LIBREOFFICE=false
RUN if [ "$INSTALL_LIBREOFFICE" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libreoffice-writer-nogui \
      && rm -rf /var/lib/apt/lists/*; fi

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY evals ./evals
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
# PORT is set by hosts such as Render/Railway; defaults to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
