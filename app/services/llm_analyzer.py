import json
import re
import google.generativeai as genai
from app.config import settings

MODEL_NAME = "gemini-flash-latest"
_model = None

def get_model():
    global _model
    if _model is None:
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not set. Add it to your .env file.")
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _model = genai.GenerativeModel(MODEL_NAME)
    return _model


EXTRACTION_PROMPT = """You are an expert Indian legal analyst. 
Analyze the following Indian court judgment text and extract structured data precisely.

Return EXACTLY this JSON structure:
{{
  "case_details": {{
    "case_title": "Verbatim title of the case",
    "case_number": "Verbatim case number(s)",
    "court": "Name of the court",
    "date_of_order": "Date in DD Month YYYY format",
    "bench": ["Name of Judge 1", "Name of Judge 2"]
  }},
  "parties": {{
    "petitioner": ["Name 1", "Name 2"],
    "respondents": ["Name 1", "Name 2"]
  }},
  "key_issues": [
    "Issue 1",
    "Issue 2"
  ],
  "directions_summary": [
    "Direction 1",
    "Direction 2"
  ],
  "deadlines": [
    {{
      "event": "Description of event",
      "timeline": "Timeline mentioned"
    }}
  ],
  "source_reference": [
    {{"text": "Verbatim snippet", "page": 1}}
  ],
  "confidence_score": 0.0
}}

STRICT RULES:
- Return ONLY valid JSON.
- If a field is not found, use null or empty array.
- confidence_score should be between 0 and 1.
- No markdown fences.

JUDGMENT TEXT:
{text}"""


ACTION_PLAN_PROMPT = """You are an AI system that converts court judgment directions into structured, actionable government tasks.

TASK:
For each direction, generate a clear, executable action.

RULES:
1. ALWAYS generate at least one action per valid directive.
2. Separate ACTION from DEADLINE (e.g., "Submit compliance report within 4 weeks").
3. DO NOT hallucinate deadlines; if not explicitly stated, use "Not specified".
4. ALWAYS include a deliverable (e.g., "Written compliance report filed before the court").
5. Priority logic: High (mandatory/shall), Medium (structural/recommended), Low (observations).
6. Confidence logic: High (clear directive), Medium (reasonable inference), Low (ambiguous).
7. Department mapping: Prison/jail -> State Prison Department; Central policy -> Ministry of Home Affairs; State compliance -> State Home Department.
8. Verb + Object + Outcome format for actions.

OUTPUT FORMAT (STRICT JSON):
[
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


EXTRACTED DATA:
{extracted_data}"""


def _clean_json(text: str) -> str:
    """Strip markdown fences and whitespace."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text)
    text = re.sub(r"```$", "", text)
    return text.strip()


def extract_judgment_data(text: str) -> dict:
    """Send judgment text to Gemini, return structured extraction."""
    model = get_model()
    prompt = EXTRACTION_PROMPT.format(text=text[:30000])
    
    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.1,
            response_mime_type="application/json",
        )
    )
    
    raw = _clean_json(response.text)
    return json.loads(raw)


def generate_action_plan(extracted: dict) -> dict:
    """Generate action plan from extracted judgment data."""
    model = get_model()
    prompt = ACTION_PLAN_PROMPT.format(
        extracted_data=json.dumps(extracted, indent=2)
    )
    
    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.1,
            response_mime_type="application/json",
        )
    )
    
    raw = _clean_json(response.text)
    return json.loads(raw)
