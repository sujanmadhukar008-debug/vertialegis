import json
import shutil
import re
import traceback
from pathlib import Path
import os
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

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
        judgment.extraction_status = "pending"
        judgment.action_status = "pending"
        db.commit()

        result     = extract_text_from_pdf(pdf_path)
        full_text  = result["full_text"]
        page_count = result["page_count"]

        judgment.raw_text   = full_text
        judgment.page_count = page_count
        db.commit()

        if not full_text.strip():
            judgment.status = "rejected"
            judgment.extraction_status = "failed"
            db.commit()
            return

        # 2. LLM extraction
        from app.services.llm_analyzer import analyze_judgment_unified
        try:
            extracted = analyze_judgment_unified(full_text)
            if extracted.get("generation_mode") == "failed":
                judgment.extraction_status = "failed"
                judgment.status = "failed"
                # Store the error in ed for frontend
                ed = models.ExtractedData(
                    judgment_id = judgment_id,
                    case_number = json.dumps({"original": "N/A", "current": "N/A", "history": [], "error": extracted.get("error")}),
                    case_title  = json.dumps({"original": "N/A", "current": "N/A", "history": []})
                )
                db.add(ed)
                db.commit()
                return
            judgment.extraction_status = "success"
        except Exception as e:
            traceback.print_exc()
            judgment.extraction_status = "failed"
            judgment.status = "failed"
            db.commit()
            return

        # 2.1 Audit Logging
        ai_log = models.AILog(
            judgment_id = judgment_id,
            model_name  = extracted.get("_audit", {}).get("model", "unknown"),
            input_text  = full_text[:10000],
            output_text = json.dumps(extracted)
        )
        db.add(ai_log)

        # 3. Store extracted data with versioned history
        cd    = extracted.get("case_details", {})
        par   = extracted.get("parties", {})
        
        def wrap_versioned(val):
            return json.dumps({
                "original": val, 
                "current": val, 
                "history": []
            })

        ed = models.ExtractedData(
            judgment_id      = judgment_id,
            version          = 1,
            case_number      = wrap_versioned(cd.get("case_number")),
            case_title       = wrap_versioned(cd.get("case_title")),
            court_name       = wrap_versioned(cd.get("court")),
            date_of_order    = wrap_versioned(cd.get("date_of_order")),
            petitioners      = json.dumps([{"original": p, "current": p, "history": []} for p in par.get("petitioner", [])]),
            respondents      = json.dumps([{"original": r, "current": r, "history": []} for r in par.get("respondents", [])]),
            directions       = json.dumps([{"original": d, "current": d, "history": []} for d in extracted.get("directions", [])]),
            timelines        = json.dumps([]),
            flags            = json.dumps({}),
            confidence_score = extracted.get("_audit", {}).get("confidence", 0.8),
            source_highlights= json.dumps(extracted.get("directions", [])),
        )
        db.add(ed)

        # 4. Generate draft Action Plan immediately
        actions = extracted.get("actions", [])
        if actions:
            judgment.action_status = "success"
            has_high = any((a.get("priority") or "").lower() == "high" for a in actions)
            overall_priority = "high" if has_high else "medium"

            ap = models.ActionPlan(
                judgment_id             = judgment_id,
                version                 = 1,
                nature_of_action        = "compliance",
                priority_level          = overall_priority,
                key_timelines           = json.dumps([]),
                responsible_departments = json.dumps(list(set([a.get("department") for a in actions if a.get("department")]))),
                specific_actions        = json.dumps([{"original": a, "current": a, "history": []} for a in actions]),
                status                  = "draft",
            )
            db.add(ap)
        else:
            judgment.action_status = "failed"

        judgment.status = "extracted"
        db.commit()

    except Exception:
        traceback.print_exc()
        try:
            judgment.status = "failed"
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


# ── AI Audit Log ──────────────────────────────────────────────────────────────
@app.get("/api/judgments/{judgment_id}/ai-log")
def get_ai_log(judgment_id: int, db: Session = Depends(get_db)):
    log = db.query(models.AILog).filter(models.AILog.judgment_id == judgment_id).order_by(models.AILog.timestamp.desc()).first()
    if not log:
        raise HTTPException(404, "AI log not found")
    
    output = json.loads(log.output_text) if log.output_text else {}
    
    # Decision Summary
    summary = []
    for action in output.get("actions", []):
        text = (action.get("source_text") or "").lower()
        reasons = []
        if "shall" in text or "must" in text: reasons.append("Detected mandatory 'shall/must'")
        if action.get("deadline") != "Not specified": reasons.append(f"Found deadline: {action.get('deadline')}")
        if action.get("department"): reasons.append(f"Mapped to {action.get('department')}")
        summary.append({
            "action": action.get("action"),
            "logic": reasons
        })

    return {
        "model": log.model_name,
        "timestamp": log.timestamp,
        "mode": output.get("generation_mode", "strict"),
        "retry_count": output.get("_audit", {}).get("retry_count", 0),
        "decision_summary": summary,
        "raw_output": output,
        "input_snippet": log.input_text[:1000]
    }


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
        try:
            val = json.loads(field) if field else None
            return val
        except:
            return field

    return {
        "id":          j.id,
        "filename":    j.filename,
        "upload_date": j.upload_date,
        "status":      j.status,
        "extraction_status": j.extraction_status,
        "action_status": j.action_status,
        "page_count":  j.page_count,
        "raw_text":    j.raw_text,
        "extracted_data": {
            "id":               ed.id if ed else None,
            "version":          ed.version if ed else 1,
            "case_number":      parse(ed.case_number) if ed else None,
            "case_title":       parse(ed.case_title) if ed else None,
            "court_name":       parse(ed.court_name) if ed else None,
            "date_of_order":    parse(ed.date_of_order) if ed else None,
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
                "version":                 ap.version,
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
    
    # Check if a draft action plan already exists (from unified extraction)
    existing_ap = db.query(models.ActionPlan).filter(models.ActionPlan.judgment_id == judgment_id).first()
    if existing_ap:
        def parse(f):
            try: return json.loads(f) if f else []
            except: return []
        
        return {
            "id":                      existing_ap.id,
            "version":                 existing_ap.version,
            "nature_of_action":        existing_ap.nature_of_action,
            "priority_level":          existing_ap.priority_level,
            "key_timelines":           parse(existing_ap.key_timelines),
            "responsible_departments": parse(existing_ap.responsible_departments),
            "specific_actions":        parse(existing_ap.specific_actions),
            "status":                  existing_ap.status,
            "created_at":              existing_ap.created_at,
        }

    # Fallback to manual generation if none exists (legacy or failed background)
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
        "directions":   parse(ed.directions),
    }

    from app.services.llm_analyzer import generate_action_plan
    actions = generate_action_plan(extracted_payload)
    if not isinstance(actions, list):
        actions = []

    has_high = any((a.get("priority") or "").lower() == "high" for a in actions)
    overall_priority = "high" if has_high else "medium"

    ap = models.ActionPlan(
        judgment_id             = judgment_id,
        version                 = 1,
        nature_of_action        = "compliance",
        priority_level          = overall_priority,
        key_timelines           = json.dumps([]),
        responsible_departments = json.dumps(list(set([a.get("department") for a in actions if a.get("department")]))),
        specific_actions        = json.dumps([{"original": a, "current": a, "history": []} for a in actions]),
        status                  = "draft",
    )
    db.add(ap)
    db.commit()
    db.refresh(ap)

    return {
        "id":                      ap.id,
        "version":                 ap.version,
        "nature_of_action":        ap.nature_of_action,
        "priority_level":          ap.priority_level,
        "key_timelines":           [],
        "responsible_departments": json.loads(ap.responsible_departments),
        "specific_actions":        parse(ap.specific_actions),
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
    
    ed = j.extracted_data
    if not ed:
        raise HTTPException(400, "No extracted data found")

    # Atomic Locking
    if body.version is not None and ed.version != body.version:
        raise HTTPException(409, "Record has been updated by another user. Please refresh.")

    if body.action == "approve":
        # Strict Validation
        ed_json = json.loads(ed.case_number)
        if not ed_json.get("current"):
            raise HTTPException(400, "Case number is required for approval.")
        j.status = "verified"
    elif body.action == "reject":
        j.status = "rejected"
    elif body.action == "edit" and body.changes:
        ed.version += 1
        for field, new_val in body.changes.items():
            if hasattr(ed, field):
                current_data = json.loads(getattr(ed, field))
                # Add to history
                current_data["history"].append({
                    "value": current_data["current"],
                    "timestamp": datetime.now().isoformat()
                })
                current_data["current"] = new_val
                setattr(ed, field, json.dumps(current_data))
        j.status = "verified"
    else:
        raise HTTPException(400, "action must be approve | edit | reject")

    log = models.VerificationLog(
        judgment_id     = judgment_id,
        target          = "extraction",
        target_id       = ed.id,
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
