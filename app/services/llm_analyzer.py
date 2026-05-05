import json
import re
import time
import google.generativeai as genai
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ValidationError
from app.config import settings

MODEL_NAME = "gemini-flash-latest"  # Stable flash model
_model = None

def get_model():
    global _model
    if _model is None:
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not set. Add it to your .env file.")
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _model = genai.GenerativeModel(MODEL_NAME)
    return _model

# --- Pydantic Schemas for Validation ---
class CaseDetails(BaseModel):
    case_title: str = ""
    case_number: str = ""
    court: str = ""
    date_of_order: str = ""

class PartyDetails(BaseModel):
    petitioner: List[str] = []
    respondents: List[str] = []

class Direction(BaseModel):
    text: str = ""
    page: Optional[int] = None

class Action(BaseModel):
    action: str = ""
    department: str = ""
    deadline: str = "Not specified"
    deliverable: str = ""
    source_text: str = ""
    page: Optional[int] = None
    priority: str = "Medium"
    confidence: str = "Medium"
    reasoning: str = ""
    impact: str = "" # New: Why this matters (e.g., 'Required for court compliance')
    urgency_explanation: str = "" # New: Detailed logic for the deadline

class UnifiedAnalyzerResponse(BaseModel):
    case_details: CaseDetails
    parties: PartyDetails
    directions: List[Direction] = []
    actions: List[Action] = []
    generation_mode: str = "strict" # strict | fallback

# --- Authoritative Logic ---
def refine_action_deterministic(action: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """Enforce backend rules for confidence and impact."""
    text = (action.get("source_text") or "").lower()
    
    # 1. Authoritative Confidence
    if "shall" in text or "must" in text or "directed" in text or "ordered" in text:
        action["confidence"] = "High"
        action["priority"] = "High"
        action["impact"] = "Mandatory directive — Court compliance required"
    elif mode == "fallback":
        action["confidence"] = "Low"
        action["impact"] = "⚠ Fallback mode — Manual verification recommended"
    else:
        # Grounded Impact logic
        if action.get("deadline") and action.get("deadline") != "Not specified":
            action["impact"] = "Time-bound compliance required"
        else:
            action["impact"] = "Advisory / Structural action"
            
    return action

# --- Unified Prompt ---
UNIFIED_PROMPT = """You are an AI system that reads court judgment text and produces structured, actionable outputs for government decision-making.

---

INPUT:
Raw court judgment text (may be partial or noisy).

---

TASK:
Perform ALL of the following:
1. Extract structured case information
2. Identify key court directions
3. Generate actionable tasks
4. Include traceability and confidence for verification

---

OUTPUT FORMAT (STRICT JSON ONLY):
{{
  "case_details": {{
    "case_title": "",
    "case_number": "",
    "court": "",
    "date_of_order": ""
  }},
  "parties": {{
    "petitioner": [],
    "respondents": []
  }},
  "directions": [
    {{
      "text": "",
      "page": null
    }}
  ],
  "actions": [
    {{
      "action": "",
      "department": "",
      "deadline": "",
      "deliverable": "",
      "source_text": "",
      "page": null,
      "priority": "",
      "confidence": "",
      "reasoning": ""
    }}
  ]
}}

---

RULES:

### EXTRACTION
* Extract only what is present in the text.
* If missing → return null or empty list.
* Keep directions atomic (one directive per item).

### ACTION GENERATION
For EACH valid direction:
1. Generate at least ONE action.
2. Use format: Verb + Object + Outcome.
3. Assign responsible department (e.g., State Prison Department, Ministry of Home Affairs).
4. Extract deadline ONLY if explicitly mentioned. Otherwise: "Not specified".
5. Always include a deliverable.
6. Link action to source_text.
7. Include reasoning for the action.

### PRIORITY LOGIC (Guidelines for LLM)
* High → contains "shall", "must", "directed".
* Medium → recommendation / structural.
* Low → observation.

### CONFIDENCE LOGIC (Guidelines for LLM)
* High → clear directive.
* Medium → inferred.
* Low → ambiguous.

### STRICT CONSTRAINTS
* DO NOT hallucinate dates or deadlines.
* DO NOT output vague phrases like "immediate action", "ongoing", or "action required".
* DO NOT return empty "actions" if directions exist.
* ALWAYS return valid JSON.

---

JUDGMENT TEXT:
{text}"""

def _clean_json(text: str) -> str:
    """Strip markdown fences and whitespace."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text)
    text = re.sub(r"```$", "", text)
    return text.strip()

def analyze_judgment_unified(text: str, retries: int = 2) -> Dict[str, Any]:
    """
    Send judgment text to Gemini, return structured unified extraction.
    Includes robust parsing, validation, and retry logic.
    """
    model = get_model()
    # Truncate text to fit within context limits while keeping bulk of content
    truncated_text = text[:40000] 
    prompt = UNIFIED_PROMPT.format(text=truncated_text)
    
    last_error = None
    for attempt in range(retries + 1):
        mode = "strict" if attempt == 0 else "fallback"
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1 if mode == "strict" else 0.4,
                    response_mime_type="application/json",
                )
            )
            
            raw_json = _clean_json(response.text)
            data = json.loads(raw_json)
            
            # Validation using Pydantic
            validated_data = UnifiedAnalyzerResponse(**data)
            validated_data.generation_mode = mode
            
            # Post-process with Authoritative Logic
            result = validated_data.dict()
            for action in result["actions"]:
                refine_action_deterministic(action, mode)
            
            # Add audit info
            result["_audit"] = {
                "model": MODEL_NAME,
                "timestamp": time.time(),
                "attempt": attempt + 1,
                "retry_count": attempt,
                "generation_mode": mode
            }
            return result

        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            print(f"Extraction attempt {attempt + 1} failed ({mode}): {e}")
            if attempt < retries:
                time.sleep(1) # Small backoff
                continue
    
    # Final fail-safe: Return empty structure if all retries fail
    print(f"All extraction attempts failed. Last error: {last_error}")
    return UnifiedAnalyzerResponse(
        case_details=CaseDetails(),
        parties=PartyDetails(),
        directions=[],
        actions=[],
        generation_mode="failed"
    ).dict()

# Keep legacy functions for backward compatibility but route to unified
def extract_judgment_data(text: str) -> dict:
    """Legacy wrapper for extraction."""
    return analyze_judgment_unified(text)

def generate_action_plan(extracted_data: dict) -> list:
    """Legacy wrapper for action plan generation."""
    # Since unified prompt does both, if this is called after extraction, 
    # we should check if actions are already in the extracted_data.
    if "actions" in extracted_data:
        return extracted_data["actions"]
    return []

