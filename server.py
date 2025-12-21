"""
PDFMathTranslate HTTP Server for Railway
Provides REST API for PDF translation using pdf2zh

Endpoints:
- POST /translate - Submit a PDF for translation
- GET /status/<job_id> - Check translation status
- GET /health - Health check
"""

import os
import json
import uuid
import threading
import requests
from flask import Flask, request, jsonify, send_file
from pdf2zh import translate_file
import tempfile
import shutil

app = Flask(__name__)

# In-memory job storage (use Redis for production)
jobs = {}

# S3/Cloud storage config (optional)
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "")
AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

def upload_to_s3(file_path, key):
    """Upload file to S3 and return public URL"""
    try:
        import boto3
        s3 = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY
        )
        s3.upload_file(file_path, STORAGE_BUCKET, key, ExtraArgs={'ACL': 'public-read'})
        return f"https://{STORAGE_BUCKET}.s3.amazonaws.com/{key}"
    except Exception as e:
        print(f"S3 upload failed: {e}")
        return None

def translate_pdf_async(job_id, pdf_url, target_lang, callback_url, book_id):
    """Background task to translate PDF"""
    try:
        jobs[job_id]["status"] = "processing"
        
        # Download PDF
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_input:
            print(f"[Job {job_id}] Downloading PDF from {pdf_url}")
            response = requests.get(pdf_url, timeout=300)
            response.raise_for_status()
            tmp_input.write(response.content)
            input_path = tmp_input.name
        
        # Translate using pdf2zh
        print(f"[Job {job_id}] Starting translation to {target_lang}")
        output_path = input_path.replace(".pdf", f"_{target_lang}.pdf")
        
        translate_file(
            input_path,
            output_path,
            lang_in="auto",
            lang_out=target_lang,
            service="google",  # Can be changed to openai, etc.
        )
        
        # Upload translated PDF
        if STORAGE_BUCKET and AWS_ACCESS_KEY:
            translated_url = upload_to_s3(output_path, f"translations/{book_id}/{job_id}.pdf")
        else:
            # For local testing, keep file path
            translated_url = f"/download/{job_id}"
            shutil.move(output_path, f"/tmp/{job_id}.pdf")
        
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["translated_url"] = translated_url
        
        # Cleanup
        os.unlink(input_path)
        if os.path.exists(output_path):
            os.unlink(output_path)
        
        # Send callback
        if callback_url:
            try:
                requests.post(callback_url, json={
                    "bookId": book_id,
                    "status": "completed",
                    "translatedFileUrl": translated_url
                }, timeout=30)
                print(f"[Job {job_id}] Callback sent successfully")
            except Exception as e:
                print(f"[Job {job_id}] Callback failed: {e}")
        
        print(f"[Job {job_id}] Translation completed")
        
    except Exception as e:
        print(f"[Job {job_id}] Translation failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        
        # Send failure callback
        if callback_url:
            try:
                requests.post(callback_url, json={
                    "bookId": book_id,
                    "status": "failed",
                    "error": str(e)
                }, timeout=30)
            except:
                pass

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "pdf-translate"})

@app.route("/translate", methods=["POST"])
def translate():
    data = request.json
    
    book_id = data.get("bookId")
    pdf_url = data.get("pdfUrl")
    target_lang = data.get("targetLang", "zh")
    callback_url = data.get("callbackUrl")
    
    if not pdf_url:
        return jsonify({"error": "pdfUrl is required"}), 400
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "pending",
        "book_id": book_id,
        "created_at": str(__import__("datetime").datetime.now())
    }
    
    # Start translation in background
    thread = threading.Thread(
        target=translate_pdf_async,
        args=(job_id, pdf_url, target_lang, callback_url, book_id)
    )
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
        "translatedUrl": job.get("translated_url"),
        "error": job.get("error")
    })

@app.route("/download/<job_id>", methods=["GET"])
def download(job_id):
    """Download translated PDF (for local testing)"""
    file_path = f"/tmp/{job_id}.pdf"
    if os.path.exists(file_path):
        return send_file(file_path, mimetype="application/pdf")
    return jsonify({"error": "File not found"}), 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
