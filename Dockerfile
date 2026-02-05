FROM python:3.12-slim-bookworm

# Install system dependencies for BabelDOC and its ML components
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libfontconfig1 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install uv for package management (recommended by BabelDOC)
RUN pip install --no-cache-dir uv

# Install BabelDOC using uv
RUN uv pip install --system BabelDOC

# Install other Python dependencies
RUN pip install --no-cache-dir flask gunicorn flask-cors requests boto3

WORKDIR /app
COPY server.py .

ENV PORT=8080

# Increase timeout for long-running translations
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 1200 --workers 2 server:app
