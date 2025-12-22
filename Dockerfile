FROM python:3.11-slim-bookworm

# Install system dependencies for pdf2zh and its ML components
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

# Install Python packages step by step for better error messages
RUN pip install --no-cache-dir flask gunicorn flask-cors requests

# Install pdf2zh separately to see any errors
RUN pip install --no-cache-dir pdf2zh || echo "pdf2zh installation warning, continuing..."

WORKDIR /app
COPY server.py .

ENV PORT=8080

CMD gunicorn --bind 0.0.0.0:$PORT --timeout 1200 --workers 1 server:app
