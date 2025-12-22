FROM python:3.11-slim

# Install system dependencies for pdf2zh
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    poppler-utils \
    libgl1-mesa-glx \
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

# Use exec form for gunicorn with workers
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 1200 --workers 2 server:app
