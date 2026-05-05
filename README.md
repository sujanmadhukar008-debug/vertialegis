# CCMS AI — Court Compliance Management System

AI-powered system that extracts structured data from Indian court judgment PDFs,
generates government action plans, enables human verification, and displays
trusted results on a premium dark dashboard.

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Gemini API key
cp .env.example .env
# Edit .env and set: ANTHROPIC_API_KEY=AIza...your_key

# 3. Run
python main.py
# or: uvicorn main:app --reload

# 4. Open browser
# http://localhost:8000
```

---

## Full workflow

| Step | What to do |
|------|-----------|
| 1 | Upload a court judgment PDF via the Upload page |
| 2 | Wait for AI extraction (~30-60 sec depending on PDF size) |
| 3 | Click "Pending Review" → review extracted data |
| 4 | Approve (auto-generates action plan) or Reject |
| 5 | Review action plan → Approve or Reject |
| 6 | Approved plans appear on the Dashboard (trusted layer) |

---

## Project structure

```
ccms_ai_prototype/
├── main.py                     # FastAPI — all API endpoints
├── requirements.txt
├── .env                        # Your secrets (ANTHROPIC_API_KEY)
├── .env.example
├── ccms.db                     # SQLite (auto-created)
├── uploads/                    # Uploaded PDFs
├── app/
│   ├── config.py               # Settings (reads .env)
│   ├── database.py             # SQLAlchemy setup
│   ├── models.py               # DB models
│   ├── schemas.py              # Pydantic schemas
│   └── services/
│       ├── pdf_extractor.py    # pdfplumber + PyMuPDF
│       └── llm_analyzer.py     # Claude Sonnet 4 (claude-sonnet-4-20250514) integration
└── static/
    ├── index.html              # Dashboard SPA
    ├── css/styles.css          # Premium dark theme
    └── js/app.js               # Routing + API calls + UI logic
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/` | Dashboard UI |
| POST | `/api/upload` | Upload PDF → background extraction |
| GET  | `/api/judgments` | List all (filter by ?status=) |
| GET  | `/api/judgments/{id}` | Full judgment + extracted data + plans |
| POST | `/api/judgments/{id}/action-plan` | Generate action plan |
| PUT  | `/api/judgments/{id}/verify` | Approve/Edit/Reject extraction |
| PUT  | `/api/action-plans/{id}/verify` | Approve/Reject action plan |
| GET  | `/api/dashboard/stats` | Aggregate counts |
| GET  | `/api/dashboard/approved` | Trusted approved records only |

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| ANTHROPIC_API_KEY | ✅ | — | From console.anthropic.com |
| DATABASE_URL | ❌ | sqlite:///./ccms.db | SQLite path |
| UPLOAD_DIR | ❌ | ./uploads | PDF storage directory |
| OCR_ENABLED | ❌ | false | Enable Tesseract OCR for scanned PDFs |

---

## Free tier limits (Claude Sonnet 4 (claude-sonnet-4-20250514))
- 10 requests/minute
- 250 requests/day
- Sufficient for prototype testing

---

## Next steps for production
1. Replace SQLite with PostgreSQL
2. Add authentication (Supabase / Firebase)
3. Add email/SMS alerts for approaching deadlines
4. Add Tesseract OCR for scanned PDFs (`sudo apt install tesseract-ocr`)
5. Deploy with Docker on a government server
