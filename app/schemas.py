from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime

# ── Shared ────────────────────────────────────────────────────────────────────
class FieldWithSource(BaseModel):
    value: Optional[str] = None
    source_text: Optional[str] = None

# ── Judgment ──────────────────────────────────────────────────────────────────
class JudgmentBase(BaseModel):
    filename: str

class JudgmentOut(BaseModel):
    id: int
    filename: str
    upload_date: datetime
    status: str
    page_count: int
    pdf_path: Optional[str] = None

    class Config:
        from_attributes = True

class JudgmentDetail(JudgmentOut):
    raw_text: Optional[str] = None
    extracted_data: Optional[Any] = None
    action_plans: Optional[List[Any]] = []

# ── Extracted Data ─────────────────────────────────────────────────────────────
class ExtractedDataOut(BaseModel):
    id: int
    judgment_id: int
    case_number: Optional[str]
    case_title: Optional[str]
    court_name: Optional[str]
    date_of_order: Optional[str]
    petitioners: Optional[str]
    respondents: Optional[str]
    directions: Optional[str]
    timelines: Optional[str]
    flags: Optional[str]
    confidence_score: Optional[float]
    source_highlights: Optional[str]

    class Config:
        from_attributes = True

# ── Action Plan ───────────────────────────────────────────────────────────────
class ActionPlanOut(BaseModel):
    id: int
    judgment_id: int
    nature_of_action: Optional[str]
    key_timelines: Optional[str]
    responsible_departments: Optional[str]
    specific_actions: Optional[str]
    priority_level: Optional[str]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

# ── Verification ──────────────────────────────────────────────────────────────
class VerifyRequest(BaseModel):
    action: str                         # approve | edit | reject
    notes: Optional[str] = ""
    changes: Optional[dict] = {}
    version: Optional[int] = None       # For atomic updates

# ── Dashboard ─────────────────────────────────────────────────────────────────
class DashboardStats(BaseModel):
    total: int
    pending: int
    extracted: int
    verified: int
    rejected: int
    approved_plans: int
