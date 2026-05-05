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
    """Strip markdown blocks and common LLM noise."""
    # Strip markdown code blocks if present
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    
    text = text.strip()
    # Remove common trailing comma issues before closing braces/brackets
    text = re.sub(r',\s*([\]}])', r'\1', text)
    return text

def analyze_judgment_unified(text: str, retries: int = 2) -> Dict[str, Any]:
    """
    Send judgment text to Gemini, return structured unified extraction.
    Includes robust parsing, validation, and exhaustive debug logging.
    """
    model = get_model()
    truncated_text = text[:40000] 
    prompt = UNIFIED_PROMPT.format(text=truncated_text)
    
    last_error = None
    for attempt in range(retries + 1):
        mode = "strict" if attempt == 0 else "fallback"
        print(f"\n--- AI EXTRACTION ATTEMPT {attempt + 1} ({mode}) ---")
        
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1 if mode == "strict" else 0.4,
                    response_mime_type="application/json",
                )
            )
            
            raw_text = response.text
            print(f"RAW LLM RESPONSE (Length: {len(raw_text)} chars):\n{raw_text[:2000]}...") # Log first 2k chars
            
            cleaned_json = _clean_json(raw_text)
            print(f"CLEANED JSON SNIPPET:\n{cleaned_json[:500]}...")

            data = json.loads(cleaned_json)
            print("JSON PARSE: SUCCESS")

            # Validation using Pydantic
            try:
                validated_data = UnifiedAnalyzerResponse(**data)
                validated_data.generation_mode = mode
                print("PYDANTIC VALIDATION: SUCCESS")
            except ValidationError as ve:
                print(f"PYDANTIC VALIDATION ERROR:\n{ve}")
                # If validation fails but it's valid JSON, we might still want to try and salvage 
                # or just force a fallback retry
                if attempt < retries: continue
                raise ve

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
                "generation_mode": mode,
                "response_length": len(raw_text)
            }
            return result

        except Exception as e:
            last_error = str(e)
            print(f"CRITICAL ERROR in attempt {attempt + 1}: {last_error}")
            if attempt < retries:
                time.sleep(1)
                continue
    
    # FINAL STABILIZATION FAIL-SAFE:
    # Return a valid structure so the UI/DB doesn't crash, but mark it as failed
    print("ALL ATTEMPTS FAILED. Returning safe failure response.")
    return {
        "case_details": {"case_title": "Processing Failed", "case_number": "", "court": "", "date_of_order": ""},
        "parties": {"petitioner": [], "respondents": []},
        "directions": [],
        "actions": [],
        "generation_mode": "failed",
        "error": last_error,
        "_audit": {"status": "failed", "last_error": last_error}
    }

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

