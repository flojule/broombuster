# syntax=docker/dockerfile:1
# Builds for linux/amd64 and linux/arm64 (Raspberry Pi 5).
# Build with: docker buildx build --platform linux/arm64 -t broombuster .

FROM python:3.12-slim

WORKDIR /app

# System deps for GeoPandas / Shapely / PyProj.
# Pre-built wheels exist for arm64; libgdal-dev is for the rare source-build fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so Docker layer cache survives source changes.
COPY pyproject.toml ./pyproject.toml
COPY src/           ./src/
RUN pip install --no-cache-dir '.[api]'

COPY frontend/ ./frontend/
COPY data/     ./data/

# /data volume — SQLite DB and cached city data land here at runtime.
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "broombuster.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
