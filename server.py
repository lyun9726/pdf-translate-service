"""
Minimal PDF Translate Server for Railway
"""

import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "pdf-translate"})

@app.route("/", methods=["GET"])
def root():
    return jsonify({"message": "PDF Translate Service is running"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
