"""
PDFMathTranslate HTTP Server for Railway
Using CLI interface since Python API is not well documented
"""

import os
import sys
import uuid
import threading
import requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import tempfile
import shutil
import time
import subprocess
import logging

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def log(msg):
    """Print with flush for Railway logging"""
    print(f"[Server] {msg}", flush=True)
    logger.info(msg)

log("Starting initialization...")
log(f"Python version: {sys.version}")

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# In-memory job storage
jobs = {}

# Check if pdf2zh CLI is available
pdf2zh_available = False
pdf2zh_error = None

log("Checking pdf2zh CLI...")
try:
    result = subprocess.run(
        ["pdf2zh", "--help"],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode == 0 or "usage" in result.stdout.lower() or "usage" in result.stderr.lower():
        pdf2zh_available = True
        log("✓ pdf2zh CLI is available")
    else:
        pdf2zh_error = f"CLI returned code {result.returncode}: {result.stderr}"
        log(f"✗ pdf2zh CLI error: {pdf2zh_error}")
except FileNotFoundError:
    pdf2zh_error = "pdf2zh command not found"
    log(f"✗ {pdf2zh_error}")
except Exception as e:
    pdf2zh_error = str(e)
    log(f"✗ pdf2zh check failed: {e}")

def translate_pdf_async(job_id, pdf_url, target_lang, callback_url, book_id):
    """Background task to translate PDF using CLI"""
    
    try:
        if not pdf2zh_available:
            raise Exception(f"pdf2zh is not available: {pdf2zh_error}")
        
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 10
        send_callback(callback_url, book_id, "processing", progress=10)
        
        # Create temp directory for this job
        work_dir = tempfile.mkdtemp(prefix=f"pdf2zh_{job_id}_")
        input_path = os.path.join(work_dir, "input.pdf")
        
        # Download PDF
        log(f"[Job {job_id}] Downloading PDF...")
        response = requests.get(pdf_url, timeout=300)
        response.raise_for_status()
        with open(input_path, 'wb') as f:
            f.write(response.content)
        
        jobs[job_id]["progress"] = 20
        send_callback(callback_url, book_id, "processing", progress=20)
        
        # Run pdf2zh CLI
        log(f"[Job {job_id}] Running pdf2zh translation to {target_lang}...")
        jobs[job_id]["progress"] = 30
        send_callback(callback_url, book_id, "processing", progress=30)
        
        # Build command with service flag
        # pdf2zh uses -s to specify translation service: google, bing, openai, etc.
        # Default to Google Translate (free) unless PDF2ZH_SERVICE env var is set
        service = os.environ.get("PDF2ZH_SERVICE", "google")
        
        cmd = [
            "pdf2zh",
            input_path,
            "-lo", target_lang,
            "-s", service,  # Use specified translation service
            "-o", work_dir
        ]
        
        # Log the command and environment
        log(f"[Job {job_id}] Command: {' '.join(cmd)}")
        log(f"[Job {job_id}] Service: {service}")
        
        # Pass environment variables (OPENAI_API_KEY, etc. are already in os.environ)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1200,  # 20 minutes timeout
            cwd=work_dir,
            env=os.environ.copy()  # Pass all environment variables to subprocess
        )
        
        if result.returncode != 0:
            raise Exception(f"pdf2zh failed: {result.stderr}")
        
        # Find output files (pdf2zh creates input-mono.pdf and input-dual.pdf)
        mono_path = os.path.join(work_dir, "input-mono.pdf")
        dual_path = os.path.join(work_dir, "input-dual.pdf")
        
        output_path = None
        if os.path.exists(mono_path):
            output_path = mono_path
        elif os.path.exists(dual_path):
            output_path = dual_path
        else:
            # Check for any PDF that's not input.pdf
            for f in os.listdir(work_dir):
                if f.endswith('.pdf') and f != 'input.pdf':
                    output_path = os.path.join(work_dir, f)
                    break
        
        if not output_path or not os.path.exists(output_path):
            raise Exception("No output PDF found after translation")
        
        jobs[job_id]["progress"] = 90
        
        # Move to permanent location
        permanent_path = f"/tmp/translated_{job_id}.pdf"
        shutil.copy2(output_path, permanent_path)
        
        # Cleanup work directory
        shutil.rmtree(work_dir, ignore_errors=True)
        
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
        
        send_callback(callback_url, book_id, "completed", translated_url=translated_url)
        log(f"[Job {job_id}] ✓ Translation completed: {translated_url}")
        
    except Exception as e:
        log(f"[Job {job_id}] ✗ Translation failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        send_callback(callback_url, book_id, "failed", error=str(e))

def send_callback(callback_url, book_id, status, progress=None, translated_url=None, error=None):
    if not callback_url:
        return
    try:
        payload = {"bookId": book_id, "status": status}
        if progress is not None:
            payload["progress"] = progress
        if translated_url:
            payload["translatedFileUrl"] = translated_url
        if error:
            payload["error"] = error
        requests.post(callback_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Callback] Failed: {e}")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "pdf-translate",
        "pdf2zh_available": pdf2zh_available,
        "pdf2zh_error": pdf2zh_error,
        "active_jobs": len(jobs)
    })

@app.route("/test-pdf2zh", methods=["GET"])
def test_pdf2zh():
    """Synchronous test to verify pdf2zh CLI works"""
    import sys
    
    if not pdf2zh_available:
        return jsonify({"error": "pdf2zh not available", "details": pdf2zh_error}), 503
    
    try:
        # Just check if pdf2zh responds
        result = subprocess.run(
            ["pdf2zh", "--version"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return jsonify({
            "success": True,
            "stdout": result.stdout[:500] if result.stdout else "",
            "stderr": result.stderr[:500] if result.stderr else "",
            "returncode": result.returncode,
            "python_version": sys.version,
            "memory_info": "Check Railway Metrics"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "message": "PDF Translate Service",
        "pdf2zh_available": pdf2zh_available,
        "pdf2zh_error": pdf2zh_error
    })

@app.route("/translate", methods=["POST"])
def translate():
    if not pdf2zh_available:
        return jsonify({
            "error": "pdf2zh is not available",
            "details": pdf2zh_error,
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

log("Routes registered")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    log(f"Starting on port {port}")
    app.run(host="0.0.0.0", port=port)
