# PDFMathTranslate Service for Railway Deployment
# 
# This is a wrapper service that runs PDFMathTranslate and provides an HTTP API
# Deploy this to Railway as a separate service
#
# Usage:
# 1. Create a new Railway project
# 2. Deploy from this Dockerfile OR use the template below
# 3. Set environment variables
# 4. Connect to your main app via PDF_TRANSLATE_SERVICE_URL

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install PDFMathTranslate
RUN pip install --no-cache-dir pdf2zh flask gunicorn requests

# Create working directory
WORKDIR /app

# Copy the server script
COPY server.py .

# Default port (Railway will override with PORT env var)
ENV PORT=8080

# Expose port
EXPOSE 8080

# Run the server - use shell form so $PORT is expanded
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 600 server:app

