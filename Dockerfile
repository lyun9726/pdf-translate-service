FROM python:3.11-slim

# Install Flask with CORS support
RUN pip install --no-cache-dir flask gunicorn flask-cors

WORKDIR /app
COPY server.py .

ENV PORT=8080

CMD gunicorn --bind 0.0.0.0:$PORT server:app
