import os
import json
import sys
from pathlib import Path

# Add current dir to path to import app
sys.path.append(os.getcwd())

from app.services.pdf_extractor import extract_text_from_pdf
from app.services.llm_analyzer import extract_judgment_data, generate_action_plan
from app.config import settings

def test():
    pdf_path = "/home/cid/Documents/sid t2/Mizo_Chief_Council_Mizoram_Thr_vs_Union_Of_India_on_13_March_2026.PDF"
    if not os.path.exists(pdf_path):
        print(f"Error: File not found at {pdf_path}")
        return

    print(f"Testing with PDF: {pdf_path}")

    print("\n1. Extracting text...")
    extraction_result = extract_text_from_pdf(pdf_path)
    text = extraction_result["full_text"]
    print(f"Extracted {len(text)} characters.")

    print("\n2. Extracting structured data via Gemini...")
    structured_data = extract_judgment_data(text)
    print("\nSTRUCTURED EXTRACTION OUTPUT:")
    print(json.dumps(structured_data, indent=2))

    print("\n3. Generating action plan via Gemini...")
    action_plan = generate_action_plan(structured_data)
    print("\nACTION PLAN OUTPUT:")
    print(json.dumps(action_plan, indent=2))

if __name__ == "__main__":
    test()
