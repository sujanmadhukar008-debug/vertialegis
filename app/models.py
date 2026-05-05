from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

class Judgment(Base):
    __tablename__ = "judgments"

    id           = Column(Integer, primary_key=True, index=True)
    filename     = Column(String(255), nullable=False)
    upload_date  = Column(DateTime(timezone=True), server_default=func.now())
    raw_text     = Column(Text)
    status       = Column(String(50), default="pending")   # pending | extracting | extracted | verified | rejected
    pdf_path     = Column(String(512))
    page_count   = Column(Integer, default=0)

    extracted_data  = relationship("ExtractedData",  back_populates="judgment", uselist=False, cascade="all, delete")
    action_plans    = relationship("ActionPlan",     back_populates="judgment", cascade="all, delete")
    verification_logs = relationship("VerificationLog", back_populates="judgment", cascade="all, delete")


class ExtractedData(Base):
    __tablename__ = "extracted_data"

    id               = Column(Integer, primary_key=True, index=True)
    judgment_id      = Column(Integer, ForeignKey("judgments.id"), unique=True)
    case_number      = Column(String(255))
    case_title       = Column(Text)
    court_name       = Column(String(255))
    date_of_order    = Column(String(100))
    petitioners      = Column(Text)   # JSON string
    respondents      = Column(Text)   # JSON string
    directions       = Column(Text)   # JSON string
    timelines        = Column(Text)   # JSON string
    flags            = Column(Text)   # JSON string
    confidence_score = Column(Float, default=0.0)
    source_highlights = Column(Text)  # JSON string

    judgment = relationship("Judgment", back_populates="extracted_data")


class ActionPlan(Base):
    __tablename__ = "action_plans"

    id                      = Column(Integer, primary_key=True, index=True)
    judgment_id             = Column(Integer, ForeignKey("judgments.id"))
    nature_of_action        = Column(String(100))   # compliance | appeal | report
    key_timelines           = Column(Text)   # JSON
    responsible_departments = Column(Text)   # JSON
    specific_actions        = Column(Text)   # JSON list of {action, dept, deadline, priority, confidence}
    priority_level          = Column(String(50), default="medium")
    status                  = Column(String(50), default="draft")   # draft | approved | rejected
    created_at              = Column(DateTime(timezone=True), server_default=func.now())

    judgment = relationship("Judgment", back_populates="action_plans")


class VerificationLog(Base):
    __tablename__ = "verification_logs"

    id              = Column(Integer, primary_key=True, index=True)
    judgment_id     = Column(Integer, ForeignKey("judgments.id"))
    target          = Column(String(50))    # extraction | action_plan
    target_id       = Column(Integer)
    reviewer_action = Column(String(50))    # approve | edit | reject
    reviewer_notes  = Column(Text)
    changes_made    = Column(Text)          # JSON
    timestamp       = Column(DateTime(timezone=True), server_default=func.now())

    judgment = relationship("Judgment", back_populates="verification_logs")
