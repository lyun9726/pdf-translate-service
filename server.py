"""
BabelDOC PDF Translation Service for Railway
Supports both full PDF translation and per-page instant translation
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
import boto3

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

# Check if babeldoc CLI is available
babeldoc_available = False
babeldoc_error = None

log("Checking babeldoc CLI...")
try:
    result = subprocess.run(
        ["babeldoc", "--help"],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode == 0 or "usage" in result.stdout.lower() or "usage" in result.stderr.lower():
        babeldoc_available = True
        log("✓ babeldoc CLI is available")
    else:
        babeldoc_error = f"CLI returned code {result.returncode}: {result.stderr}"
        log(f"✗ babeldoc CLI error: {babeldoc_error}")
except FileNotFoundError:
    babeldoc_error = "babeldoc command not found"
    log(f"✗ {babeldoc_error}")
except Exception as e:
    babeldoc_error = str(e)
    log(f"✗ babeldoc check failed: {e}")

# S3 client setup
def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        region_name=os.environ.get('AWS_REGION', 'ap-southeast-1')
    )

def upload_to_s3(file_path, key):
    """Upload file to S3 and return URL"""
    s3_client = get_s3_client()
    bucket = os.environ.get('S3_BUCKET')
    
    with open(file_path, 'rb') as f:
        s3_client.upload_fileobj(
            f, bucket, key,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
    
    region = os.environ.get('AWS_REGION', 'ap-southeast-1')
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

def check_s3_exists(key):
    """Check if a file exists in S3"""
    try:
        s3_client = get_s3_client()
        bucket = os.environ.get('S3_BUCKET')
        s3_client.head_object(Bucket=bucket, Key=key)
        
        region = os.environ.get('AWS_REGION', 'ap-southeast-1')
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    except:
        return None

def translate_page_async(job_id, pdf_url, page_number, target_lang, callback_url, book_id):
    """Background task to translate a single PDF page using BabelDOC"""
    
    try:
        if not babeldoc_available:
            raise Exception(f"babeldoc is not available: {babeldoc_error}")
        
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 10
        send_callback(callback_url, book_id, "processing", progress=10, page_number=page_number)
        
        # Check S3 cache first
        cache_key = f"books/{book_id}/translated_pages/page_{page_number}_{target_lang}.pdf"
        cached_url = check_s3_exists(cache_key)
        if cached_url:
            log(f"[Job {job_id}] Page {page_number} already cached in S3")
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["progress"] = 100
            jobs[job_id]["translated_url"] = cached_url
            send_callback(callback_url, book_id, "completed", 
                         page_number=page_number, translated_url=cached_url)
            return
        
        # Create temp directory for this job
        work_dir = tempfile.mkdtemp(prefix=f"babeldoc_{job_id}_")
        input_path = os.path.join(work_dir, "input.pdf")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        # Download PDF
        log(f"[Job {job_id}] Downloading PDF...")
        response = requests.get(pdf_url, timeout=300)
        response.raise_for_status()
        with open(input_path, 'wb') as f:
            f.write(response.content)
        
        jobs[job_id]["progress"] = 20
        send_callback(callback_url, book_id, "processing", progress=20, page_number=page_number)
        
        # Run BabelDOC for single page translation
        log(f"[Job {job_id}] Translating page {page_number} to {target_lang}...")
        jobs[job_id]["progress"] = 30
        
        # Get API configuration
        openai_api_key = os.environ.get('OPENAI_API_KEY', '')
        openai_base_url = os.environ.get('OPENAI_BASE_URL', 'https://api.deepseek.com/v1')
        openai_model = os.environ.get('OPENAI_MODEL', 'deepseek-chat')
        
        if not openai_api_key:
            raise Exception("OPENAI_API_KEY not configured")
        
        # Build BabelDOC command
        cmd = [
            "babeldoc",
            "--files", input_path,
            "--pages", str(page_number),  # Only translate specified page
            "--lang-out", target_lang,
            "--openai",
            "--openai-api-key", openai_api_key,
            "--openai-base-url", openai_base_url,
            "--openai-model", openai_model,
            "--watermark-output-mode", "no_watermark",
            "--only-include-translated-page",  # Only output the translated page
            "--auto-enable-ocr-workaround",  # Auto-detect and handle scanned PDFs
            "--no-dual",  # Only generate mono (translated) version
            "--working-dir", output_dir,
        ]
        
        # Log the command (hide API key)
        safe_cmd = [c if c != openai_api_key else "***API_KEY***" for c in cmd]
        log(f"[Job {job_id}] Command: {' '.join(safe_cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes timeout for single page
            cwd=work_dir,
            env=os.environ.copy()
        )
        
        if result.returncode != 0:
            log(f"[Job {job_id}] BabelDOC stderr: {result.stderr}")
            raise Exception(f"babeldoc failed: {result.stderr[:500]}")
        
        jobs[job_id]["progress"] = 80
        
        # Find output files
        log(f"[Job {job_id}] Looking for output files in {output_dir}")
        output_files = []
        for root, dirs, files in os.walk(output_dir):
            for f in files:
                if f.endswith('.pdf'):
                    output_files.append(os.path.join(root, f))
                    log(f"[Job {job_id}] Found: {os.path.join(root, f)}")
        
        if not output_files:
            # Also check work_dir
            for f in os.listdir(work_dir):
                if f.endswith('.pdf') and f != 'input.pdf':
                    output_files.append(os.path.join(work_dir, f))
                    log(f"[Job {job_id}] Found in work_dir: {f}")
        
        if not output_files:
            raise Exception("No output PDF found after translation")
        
        # Use the first output file (should be the mono/translated version)
        output_path = output_files[0]
        log(f"[Job {job_id}] Using output: {output_path}")
        
        jobs[job_id]["progress"] = 90
        
        # Upload to S3
        log(f"[Job {job_id}] Uploading to S3...")
        translated_url = upload_to_s3(output_path, cache_key)
        
        # Cleanup work directory
        shutil.rmtree(work_dir, ignore_errors=True)
        
        # Update job status
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["translated_url"] = translated_url
        
        send_callback(callback_url, book_id, "completed", 
                     page_number=page_number, translated_url=translated_url)
        log(f"[Job {job_id}] ✓ Page {page_number} translation completed: {translated_url}")
        
    except Exception as e:
        log(f"[Job {job_id}] ✗ Translation failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        send_callback(callback_url, book_id, "failed", 
                     page_number=page_number, error=str(e))
        
        # Cleanup on error
        if 'work_dir' in locals():
            shutil.rmtree(work_dir, ignore_errors=True)


def translate_full_pdf_async(job_id, pdf_url, target_lang, callback_url, book_id):
    """Background task to translate full PDF using BabelDOC (for backward compatibility)"""
    
    try:
        if not babeldoc_available:
            raise Exception(f"babeldoc is not available: {babeldoc_error}")
        
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 10
        send_callback(callback_url, book_id, "processing", progress=10)
        
        # Create temp directory for this job
        work_dir = tempfile.mkdtemp(prefix=f"babeldoc_{job_id}_")
        input_path = os.path.join(work_dir, "input.pdf")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        # Download PDF
        log(f"[Job {job_id}] Downloading PDF...")
        response = requests.get(pdf_url, timeout=300)
        response.raise_for_status()
        with open(input_path, 'wb') as f:
            f.write(response.content)
        
        jobs[job_id]["progress"] = 20
        send_callback(callback_url, book_id, "processing", progress=20)
        
        # Run BabelDOC
        log(f"[Job {job_id}] Running BabelDOC translation to {target_lang}...")
        jobs[job_id]["progress"] = 30
        send_callback(callback_url, book_id, "processing", progress=30)
        
        # Get API configuration
        openai_api_key = os.environ.get('OPENAI_API_KEY', '')
        openai_base_url = os.environ.get('OPENAI_BASE_URL', 'https://api.deepseek.com/v1')
        openai_model = os.environ.get('OPENAI_MODEL', 'deepseek-chat')
        
        if not openai_api_key:
            raise Exception("OPENAI_API_KEY not configured")
        
        # Build command
        cmd = [
            "babeldoc",
            "--files", input_path,
            "--lang-out", target_lang,
            "--openai",
            "--openai-api-key", openai_api_key,
            "--openai-base-url", openai_base_url,
            "--openai-model", openai_model,
            "--watermark-output-mode", "no_watermark",
            "--auto-enable-ocr-workaround",
            "--no-dual",  # Only mono version for full translation
            "--working-dir", output_dir,
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 60 minutes timeout for full PDF
            cwd=work_dir,
            env=os.environ.copy()
        )
        
        if result.returncode != 0:
            raise Exception(f"babeldoc failed: {result.stderr[:500]}")
        
        # Find output files
        output_files = []
        for root, dirs, files in os.walk(output_dir):
            for f in files:
                if f.endswith('.pdf'):
                    output_files.append(os.path.join(root, f))
        
        if not output_files:
            for f in os.listdir(work_dir):
                if f.endswith('.pdf') and f != 'input.pdf':
                    output_files.append(os.path.join(work_dir, f))
        
        if not output_files:
            raise Exception("No output PDF found after translation")
        
        output_path = output_files[0]
        
        jobs[job_id]["progress"] = 90
        
        # Upload to S3
        log(f"[Job {job_id}] Uploading to S3...")
        timestamp = int(time.time())
        s3_key = f"books/{book_id}/translated_{timestamp}.pdf"
        translated_url = upload_to_s3(output_path, s3_key)
        
        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)
        
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["translated_url"] = translated_url
        
        send_callback(callback_url, book_id, "completed", translated_url=translated_url)
        log(f"[Job {job_id}] ✓ Full translation completed: {translated_url}")
        
    except Exception as e:
        log(f"[Job {job_id}] ✗ Translation failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        send_callback(callback_url, book_id, "failed", error=str(e))
        
        if 'work_dir' in locals():
            shutil.rmtree(work_dir, ignore_errors=True)


def send_callback(callback_url, book_id, status, progress=None, translated_url=None, 
                  error=None, page_number=None):
    if not callback_url:
        log(f"[Callback] No callback URL provided for book {book_id}")
        return
    try:
        payload = {"bookId": book_id, "status": status}
        if progress is not None:
            payload["progress"] = progress
        if translated_url:
            payload["translatedFileUrl"] = translated_url
            payload["translatedUrl"] = translated_url  # Also send as translatedUrl for compatibility
        if error:
            payload["error"] = error
        if page_number is not None:
            payload["pageNumber"] = page_number
        
        log(f"[Callback] Sending to {callback_url}")
        log(f"[Callback] Payload: {payload}")
        
        # Add Vercel protection bypass header if available
        headers = {"Content-Type": "application/json"}
        bypass_secret = os.environ.get("VERCEL_PROTECTION_BYPASS")
        if bypass_secret:
            headers["x-vercel-protection-bypass"] = bypass_secret
            log(f"[Callback] Using Vercel protection bypass")
        
        response = requests.post(callback_url, json=payload, headers=headers, timeout=30)
        log(f"[Callback] Response: {response.status_code} - {response.text[:200]}")
        
    except Exception as e:
        log(f"[Callback] Failed: {e}")


# =============================================================================
# API Routes
# =============================================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "pdf-translate-babeldoc",
        "babeldoc_available": babeldoc_available,
        "babeldoc_error": babeldoc_error,
        "active_jobs": len(jobs)
    })


@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "message": "PDF Translation Service (BabelDOC)",
        "version": "2.0.0",
        "babeldoc_available": babeldoc_available,
        "babeldoc_error": babeldoc_error,
        "endpoints": {
            "/translate": "Full PDF translation (POST)",
            "/translate/page": "Single page translation (POST)",
            "/status/<job_id>": "Check job status (GET)",
            "/download/<job_id>": "Download translated PDF (GET)"
        }
    })


@app.route("/translate/page", methods=["POST"])
def translate_page():
    """Translate a single page of a PDF - for instant translation"""
    if not babeldoc_available:
        return jsonify({
            "error": "babeldoc is not available",
            "details": babeldoc_error,
            "status": "failed"
        }), 503
    
    data = request.json or {}
    book_id = data.get("bookId")
    pdf_url = data.get("pdfUrl")
    page_number = data.get("pageNumber")
    target_lang = data.get("targetLang", "zh")
    callback_url = data.get("callbackUrl")
    
    if not pdf_url or not book_id or page_number is None:
        return jsonify({"error": "pdfUrl, bookId and pageNumber are required"}), 400
    
    # Check S3 cache first (synchronous check)
    cache_key = f"books/{book_id}/translated_pages/page_{page_number}_{target_lang}.pdf"
    cached_url = check_s3_exists(cache_key)
    if cached_url:
        log(f"[Page Translation] Page {page_number} already cached: {cached_url}")
        return jsonify({
            "status": "completed",
            "pageNumber": page_number,
            "translatedUrl": cached_url,
            "cached": True
        })
    
    # Start async translation job
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "pending",
        "progress": 0,
        "book_id": book_id,
        "page_number": page_number,
        "created_at": time.time()
    }
    
    thread = threading.Thread(
        target=translate_page_async,
        args=(job_id, pdf_url, page_number, target_lang, callback_url, book_id)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "jobId": job_id,
        "status": "processing",
        "pageNumber": page_number,
        "message": f"Page {page_number} translation started"
    })


@app.route("/translate", methods=["POST"])
def translate():
    """Translate full PDF - for backward compatibility"""
    if not babeldoc_available:
        return jsonify({
            "error": "babeldoc is not available",
            "details": babeldoc_error,
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
        target=translate_full_pdf_async,
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
        "pageNumber": job.get("page_number"),
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
