"""
PDFMathTranslate HTTP Server for Railway
Provides REST API for PDF translation using pdf2zh
"""

import os
import uuid
import threading
import requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import tempfile
import shutil
import time

app = Flask(__name__)
CORS(app)

# In-memory job storage
jobs = {}

# Try to import pdf2zh
pdf2zh_available = False
translate_file = None
try:
    from pdf2zh import translate_file as tf
    translate_file = tf
    pdf2zh_available = True
    print("[Server] pdf2zh loaded successfully")
except Exception as e:
    print(f"[Server] Warning: pdf2zh not available: {e}")

def translate_pdf_async(job_id, pdf_url, target_lang, callback_url, book_id):
    """Background task to translate PDF"""
    global translate_file
    
    try:
        if not pdf2zh_available or translate_file is None:
            raise Exception("pdf2zh is not available")
        
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 10
        
        # Notify callback of progress
        send_callback(callback_url, book_id, "processing", progress=10)
        
        # Download PDF
        print(f"[Job {job_id}] Downloading PDF from {pdf_url}")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_input:
            response = requests.get(pdf_url, timeout=300)
            response.raise_for_status()
            tmp_input.write(response.content)
            input_path = tmp_input.name
        
        jobs[job_id]["progress"] = 20
        send_callback(callback_url, book_id, "processing", progress=20)
        
        # Create output path
        output_path = input_path.replace(".pdf", f"_{target_lang}.pdf")
        
        # Translate using pdf2zh
        print(f"[Job {job_id}] Starting translation to {target_lang}")
        jobs[job_id]["progress"] = 30
        send_callback(callback_url, book_id, "processing", progress=30)
        
        # Run translation
        translate_file(
            input_path,
            output_path,
            lang_in="auto",
            lang_out=target_lang,
            service="google",
        )
        
        jobs[job_id]["progress"] = 90
        
        # Move to permanent location
        permanent_path = f"/tmp/translated_{job_id}.pdf"
        shutil.move(output_path, permanent_path)
        
        # Cleanup input
        os.unlink(input_path)
        
        # Generate download URL
        base_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        if base_url:
            translated_url = f"https://{base_url}/download/{job_id}"
        else:
            translated_url = f"/download/{job_id}"
        
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["translated_url"] = translated_url
        jobs[job_id]["file_path"] = permanent_path
        
        # Send completion callback
        send_callback(callback_url, book_id, "completed", translated_url=translated_url)
        
        print(f"[Job {job_id}] Translation completed: {translated_url}")
        
    except Exception as e:
        print(f"[Job {job_id}] Translation failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        
        send_callback(callback_url, book_id, "failed", error=str(e))

def send_callback(callback_url, book_id, status, progress=None, translated_url=None, error=None):
    """Send status callback to main app"""
    if not callback_url:
        return
    
    try:
        payload = {
            "bookId": book_id,
            "status": status,
        }
        if progress is not None:
            payload["progress"] = progress
        if translated_url:
            payload["translatedFileUrl"] = translated_url
        if error:
            payload["error"] = error
        
        requests.post(callback_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Callback] Failed to send: {e}")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "pdf-translate",
        "pdf2zh_available": pdf2zh_available
    })

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "message": "PDF Translate Service is running",
        "pdf2zh_available": pdf2zh_available,
        "endpoints": ["/health", "/translate", "/status/<job_id>", "/download/<job_id>"]
    })

@app.route("/translate", methods=["POST"])
def translate():
    if not pdf2zh_available:
        return jsonify({
            "error": "pdf2zh is not available on this server",
            "status": "failed"
        }), 503
    
    data = request.json or {}
    
    book_id = data.get("bookId")
    pdf_url = data.get("pdfUrl")
    target_lang = data.get("targetLang", "zh")
    callback_url = data.get("callbackUrl")
    
    if not pdf_url:
        return jsonify({"error": "pdfUrl is required"}), 400
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "pending",
        "progress": 0,
        "book_id": book_id,
        "created_at": time.time()
    }
    
    # Start translation in background
    thread = threading.Thread(
        target=translate_pdf_async,
        args=(job_id, pdf_url, target_lang, callback_url, book_id)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "jobId": job_id,
        "status": "pending",
        "message": "Translation job started"
    })

@app.route("/status/<job_id>", methods=["GET"])
def status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    
    job = jobs[job_id]
    return jsonify({
        "jobId": job_id,
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "translatedUrl": job.get("translated_url"),
        "error": job.get("error")
    })

@app.route("/download/<job_id>", methods=["GET"])
def download(job_id):
    """Download translated PDF"""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    
    job = jobs[job_id]
    file_path = job.get("file_path")
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404
    
    return send_file(
        file_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"translated_{job_id}.pdf"
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"[Server] Starting on port {port}")
    print(f"[Server] pdf2zh available: {pdf2zh_available}")
    app.run(host="0.0.0.0", port=port)
