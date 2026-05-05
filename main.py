import json
import shutil
import re
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

from app.config import settings
from app.database import get_db, init_db
from app import models, schemas
from app.services.pdf_extractor import extract_text_from_pdf
from app.services.llm_analyzer import extract_judgment_data, generate_action_plan

# ── App init ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="CCMS AI — Court Compliance Management System", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")


# ── Runtime API key setter (from frontend) ─────────────────────────────────────
_runtime_key: str = ""

@app.post("/api/set-key")
async def set_api_key(body: dict):
    global _runtime_key
    key = body.get("key", "").strip()
    _runtime_key = key
    # Inject into the llm_analyzer module at runtime
    from app.services import llm_analyzer
    from app.config import settings
    if key:
        settings.GEMINI_API_KEY = key
        llm_analyzer._model = None  # reset lazy model so it picks up new key
    return {"ok": True}

# ── Background processing ─────────────────────────────────────────────────────
def process_judgment(judgment_id: int, pdf_path: str):
    """Run in background: extract text → LLM analysis → store results."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        judgment = db.query(models.Judgment).filter(models.Judgment.id == judgment_id).first()
        if not judgment:
            return

        # 1. Extract PDF text
        judgment.status = "extracting"
        db.commit()

        result     = extract_text_from_pdf(pdf_path)
        full_text  = result["full_text"]
        page_count = result["page_count"]

        judgment.raw_text   = full_text
        judgment.page_count = page_count
        db.commit()

        if not full_text.strip():
            judgment.status = "rejected"
            db.commit()
            return

        # 2. LLM extraction
        extracted = extract_judgment_data(full_text)

        # 3. Store extracted data
        cd    = extracted.get("case_details", {})
        par   = extracted.get("parties", {})
        
        # Mapping to old DB structure
        ed = models.ExtractedData(
            judgment_id      = judgment_id,
            case_number      = cd.get("case_number"),
            case_title       = cd.get("case_title"),
            court_name       = cd.get("court"),
            date_of_order    = cd.get("date_of_order"),
            petitioners      = json.dumps(par.get("petitioner", [])),
            respondents      = json.dumps(par.get("respondents", [])),
            directions       = json.dumps(extracted.get("directions_summary", [])),
            timelines        = json.dumps(extracted.get("deadlines", [])),
            flags            = json.dumps({}), # new prompt doesn't have flags
            confidence_score = extracted.get("confidence_score", 0),
            source_highlights= json.dumps(extracted.get("source_reference", [])),
        )
        db.add(ed)
        judgment.status = "extracted"
        db.commit()

    except Exception:
        traceback.print_exc()
        try:
            judgment.status = "rejected"
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ── Upload ─────────────────────────────────────────────────────────────────────
@app.post("/api/upload", response_model=schemas.JudgmentOut)
async def upload_judgment(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    # SEC-02: File size limit 50MB
    content_bytes = await file.read()
    if len(content_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Maximum 50MB.")

    # SEC-03: Basic PDF magic bytes check
    if content_bytes[:4] != b'%PDF':
        raise HTTPException(status_code=400, detail="Invalid PDF file.")

    # Save file
    safe_name = re.sub(r'[^\w\-_.]', '_', file.filename)
    upload_path = Path(settings.UPLOAD_DIR) / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    with open(upload_path, "wb") as f:
        f.write(content_bytes)

    # Create DB record
    judgment = models.Judgment(
        filename = file.filename,
        pdf_path = str(upload_path),
        status   = "pending",
    )
    db.add(judgment)
    db.commit()
    db.refresh(judgment)

    # Queue background processing
    background_tasks.add_task(process_judgment, judgment.id, str(upload_path))
    return judgment


# ── List judgments ─────────────────────────────────────────────────────────────
@app.get("/api/judgments", response_model=list[schemas.JudgmentOut])
def list_judgments(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    q = db.query(models.Judgment)
    if status:
        q = q.filter(models.Judgment.status == status)
    return q.order_by(models.Judgment.upload_date.desc()).all()


# ── Get single judgment ────────────────────────────────────────────────────────
@app.get("/api/judgments/{judgment_id}")
def get_judgment(judgment_id: int, db: Session = Depends(get_db)):
    j = db.query(models.Judgment).filter(models.Judgment.id == judgment_id).first()
    if not j:
        raise HTTPException(status_code=404, detail="Judgment not found")

    ed  = j.extracted_data
    aps = j.action_plans

    def parse(field):
        try: return json.loads(field) if field else []
        except: return []

    return {
        "id":          j.id,
        "filename":    j.filename,
        "upload_date": j.upload_date,
        "status":      j.status,
        "page_count":  j.page_count,
        "raw_text":    j.raw_text,
        "extracted_data": {
            "id":               ed.id if ed else None,
            "case_number":      ed.case_number if ed else None,
            "case_title":       ed.case_title if ed else None,
            "court_name":       ed.court_name if ed else None,
            "date_of_order":    ed.date_of_order if ed else None,
            "petitioners":      parse(ed.petitioners if ed else None),
            "respondents":      parse(ed.respondents if ed else None),
            "directions":       parse(ed.directions if ed else None),
            "timelines":        parse(ed.timelines if ed else None),
            "flags":            parse(ed.flags if ed else None),
            "confidence_score": ed.confidence_score if ed else None,
            "full_extraction":  parse(ed.source_highlights if ed else None),
        } if ed else None,
        "action_plans": [
            {
                "id":                      ap.id,
                "nature_of_action":        ap.nature_of_action,
                "priority_level":          ap.priority_level,
                "key_timelines":           parse(ap.key_timelines),
                "responsible_departments": parse(ap.responsible_departments),
                "specific_actions":        parse(ap.specific_actions),
                "status":                  ap.status,
                "created_at":              ap.created_at,
            }
            for ap in aps
        ],
    }


# ── Generate action plan ───────────────────────────────────────────────────────
@app.post("/api/judgments/{judgment_id}/action-plan")
def create_action_plan(judgment_id: int, db: Session = Depends(get_db)):
    j = db.query(models.Judgment).filter(models.Judgment.id == judgment_id).first()
    if not j:
        raise HTTPException(404, "Judgment not found")
    if j.status not in ("extracted", "verified"):
        raise HTTPException(400, f"Judgment must be extracted first (current: {j.status})")

    ed = j.extracted_data
    if not ed:
        raise HTTPException(400, "No extracted data found")

    def parse(f):
        try: return json.loads(f) if f else []
        except: return []

    extracted_payload = {
        "case_number":  ed.case_number,
        "case_title":   ed.case_title,
        "court_name":   ed.court_name,
        "date_of_order":ed.date_of_order,
        "petitioners":  parse(ed.petitioners),
        "respondents":  parse(ed.respondents),
        "directions":   parse(ed.directions),
        "timelines":    parse(ed.timelines),
        "flags":        parse(ed.flags),
    }

    actions = generate_action_plan(extracted_payload)
    if not isinstance(actions, list):
        actions = []

    # Calculate overall priority
    has_high = any((a.get("priority") or "").lower() == "high" for a in actions)
    overall_priority = "high" if has_high else "medium"

    ap = models.ActionPlan(
        judgment_id             = judgment_id,
        nature_of_action        = "compliance",
        priority_level          = overall_priority,
        key_timelines           = json.dumps([]),
        responsible_departments = json.dumps(list(set([a.get("department") for a in actions if a.get("department")]))),
        specific_actions        = json.dumps(actions),
        status                  = "draft",
    )
    db.add(ap)
    db.commit()
    db.refresh(ap)

    return {
        "id":                      ap.id,
        "nature_of_action":        ap.nature_of_action,
        "priority_level":          ap.priority_level,
        "key_timelines":           [],
        "responsible_departments": json.loads(ap.responsible_departments),
        "specific_actions":        actions,
        "status":                  ap.status,
        "created_at":              ap.created_at,
    }


# ── Verify extraction ──────────────────────────────────────────────────────────
@app.put("/api/judgments/{judgment_id}/verify")
def verify_judgment(
    judgment_id: int,
    body: schemas.VerifyRequest,
    db: Session = Depends(get_db)
):
    j = db.query(models.Judgment).filter(models.Judgment.id == judgment_id).first()
    if not j:
        raise HTTPException(404, "Judgment not found")

    if body.action == "approve":
        j.status = "verified"
    elif body.action == "reject":
        j.status = "rejected"
    elif body.action == "edit" and body.changes:
        ed = j.extracted_data
        if ed:
            for field, value in body.changes.items():
                if hasattr(ed, field):
                    setattr(ed, field, value)
        j.status = "verified"
    else:
        raise HTTPException(400, "action must be approve | edit | reject")

    log = models.VerificationLog(
        judgment_id     = judgment_id,
        target          = "extraction",
        target_id       = j.extracted_data.id if j.extracted_data else None,
        reviewer_action = body.action,
        reviewer_notes  = body.notes or "",
        changes_made    = json.dumps(body.changes or {}),
    )
    db.add(log)
    db.commit()
    return {"status": j.status, "message": f"Judgment {body.action}d successfully"}


# ── Verify action plan ─────────────────────────────────────────────────────────
@app.put("/api/action-plans/{plan_id}/verify")
def verify_action_plan(
    plan_id: int,
    body: schemas.VerifyRequest,
    db: Session = Depends(get_db)
):
    ap = db.query(models.ActionPlan).filter(models.ActionPlan.id == plan_id).first()
    if not ap:
        raise HTTPException(404, "Action plan not found")

    if body.action == "approve":
        ap.status = "approved"
    elif body.action == "reject":
        ap.status = "rejected"
    elif body.action == "edit" and body.changes:
        if "specific_actions" in body.changes:
            ap.specific_actions = json.dumps(body.changes["specific_actions"])
        ap.status = "approved"

    log = models.VerificationLog(
        judgment_id     = ap.judgment_id,
        target          = "action_plan",
        target_id       = plan_id,
        reviewer_action = body.action,
        reviewer_notes  = body.notes or "",
        changes_made    = json.dumps(body.changes or {}),
    )
    db.add(log)
    db.commit()
    return {"status": ap.status, "message": f"Action plan {body.action}d"}



# ── Get single action plan ─────────────────────────────────────────────────────
@app.get("/api/action-plans/{plan_id}")
def get_action_plan(plan_id: int, db: Session = Depends(get_db)):
    ap = db.query(models.ActionPlan).filter(models.ActionPlan.id == plan_id).first()
    if not ap:
        raise HTTPException(404, "Action plan not found")
    def parse(f):
        try: return json.loads(f) if f else []
        except: return []
    j = ap.judgment
    ed = j.extracted_data if j else None
    return {
        "id": ap.id,
        "judgment_id": ap.judgment_id,
        "filename": j.filename if j else None,
        "case_number": ed.case_number if ed else None,
        "court_name": ed.court_name if ed else None,
        "nature_of_action": ap.nature_of_action,
        "priority_level": ap.priority_level,
        "key_timelines": parse(ap.key_timelines),
        "responsible_departments": parse(ap.responsible_departments),
        "specific_actions": parse(ap.specific_actions),
        "status": ap.status,
        "created_at": ap.created_at,
    }

# ── Dashboard stats ────────────────────────────────────────────────────────────
@app.get("/api/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    total    = db.query(models.Judgment).count()
    pending  = db.query(models.Judgment).filter(models.Judgment.status.in_(["pending","extracting"])).count()
    extracted= db.query(models.Judgment).filter(models.Judgment.status == "extracted").count()
    verified = db.query(models.Judgment).filter(models.Judgment.status == "verified").count()
    rejected = db.query(models.Judgment).filter(models.Judgment.status == "rejected").count()
    approved_plans = db.query(models.ActionPlan).filter(models.ActionPlan.status == "approved").count()
    draft_plans    = db.query(models.ActionPlan).filter(models.ActionPlan.status == "draft").count()

    return {
        "total": total, "pending": pending, "extracted": extracted,
        "verified": verified, "rejected": rejected,
        "approved_plans": approved_plans, "draft_plans": draft_plans,
    }


# ── Approved records only (trusted layer) ──────────────────────────────────────
@app.get("/api/dashboard/approved")
def approved_records(db: Session = Depends(get_db)):
    approved_plans = (
        db.query(models.ActionPlan)
        .filter(models.ActionPlan.status == "approved")
        .order_by(models.ActionPlan.created_at.desc())
        .all()
    )
    result = []
    for ap in approved_plans:
        j  = ap.judgment
        ed = j.extracted_data if j else None
        def parse(f):
            try: return json.loads(f) if f else []
            except: return []
        result.append({
            "plan_id":       ap.id,
            "judgment_id":   ap.judgment_id,
            "filename":      j.filename if j else None,
            "case_number":   ed.case_number if ed else None,
            "case_title":    ed.case_title if ed else None,
            "court_name":    ed.court_name if ed else None,
            "date_of_order": ed.date_of_order if ed else None,
            "priority_level":ap.priority_level,
            "nature_of_action":ap.nature_of_action,
            "specific_actions": parse(ap.specific_actions),
            "responsible_departments": parse(ap.responsible_departments),
            "key_timelines": parse(ap.key_timelines),
        })
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
