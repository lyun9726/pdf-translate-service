FROM python:3.11-slim-bookworm

# Install system dependencies for pdf2zh
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir \
    flask \
    gunicorn \
    flask-cors \
    requests \
    pdf2zh

WORKDIR /app
COPY server.py .

ENV PORT=8080

# Use exec form for gunicorn with longer timeout for PDF translation
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 1200 --workers 1 server:app
