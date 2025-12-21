"""
Minimal PDF Translate Server for Railway
"""

import os
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "pdf-translate"})

@app.route("/", methods=["GET"])
def root():
    return jsonify({"message": "PDF Translate Service is running"})

@app.route("/translate", methods=["POST"])
def translate():
    # Mock response for now
    data = request.json or {}
    return jsonify({
        "status": "pending",
        "message": "PDF translation service is running but pdf2zh is not yet installed",
        "mockMode": True
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
