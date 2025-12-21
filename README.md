# PDFMathTranslate Translation Service

A microservice for translating PDFs while preserving layout, powered by [pdf2zh](https://github.com/Byaidu/PDFMathTranslate).

## Deploy to Railway

### Option 1: Deploy from Dockerfile

1. Create a new Railway project
2. Choose "Deploy from GitHub repo"
3. Select this repository and set the root directory to `services/pdf-translate`
4. Railway will auto-detect the Dockerfile

### Option 2: Deploy using railway.json

```bash
cd services/pdf-translate
railway login
railway init
railway up
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | Server port (default: 8080) |
| `STORAGE_BUCKET` | No | S3 bucket for storing translated PDFs |
| `AWS_ACCESS_KEY_ID` | No | AWS access key for S3 |
| `AWS_SECRET_ACCESS_KEY` | No | AWS secret key for S3 |

## API Endpoints

### POST /translate

Submit a PDF for translation.

**Request:**
```json
{
  "bookId": "abc123",
  "pdfUrl": "https://example.com/book.pdf",
  "targetLang": "zh",
  "callbackUrl": "https://yourapp.com/api/translate/pdf/callback"
}
```

**Response:**
```json
{
  "jobId": "uuid-here",
  "status": "pending",
  "message": "Translation job started"
}
```

### GET /status/{jobId}

Check translation status.

**Response:**
```json
{
  "jobId": "uuid-here",
  "status": "completed",
  "translatedUrl": "https://bucket.s3.amazonaws.com/translations/abc123/uuid.pdf"
}
```

### GET /health

Health check endpoint.

## Integration with Main App

After deploying, add this environment variable to your Vercel deployment:

```
PDF_TRANSLATE_SERVICE_URL=https://your-railway-app.railway.app
```

## Local Development

```bash
cd services/pdf-translate
pip install -r requirements.txt
python server.py
```

## Translation Services

The service uses Google Translate by default. To use OpenAI:

1. Set `OPENAI_API_KEY` environment variable
2. Modify `server.py` to use `service="openai"` in `translate_file()`
