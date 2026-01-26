"""PDF summarization using LLM."""
import json
import os
from pathlib import Path
from typing import Optional, Dict, List
from dotenv import load_dotenv
import requests

from .pdf_extract import extract_text_from_pdf, chunk_text, ExtractedContent
from .importance import identify_important_sections, extract_action_items, extract_decisions, find_pii
from .redact import redact_pii
from .render_pdf import render_summary_pdf
from .summary_types import SummaryConfig, SummaryResult


def call_ollama(prompt: str, temperature: float = 0.2, num_predict: int = 2000) -> str:
    """Call Ollama API for summarization."""
    load_dotenv()
    base_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    
    url = f"{base_url}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_predict": num_predict
        }
    }
    
    try:
        r = requests.post(url, json=payload, timeout=300)
        r.raise_for_status()
        data = r.json()
        return data.get("response", "")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ollama API call failed: {e}")


def build_summary_prompt(content: ExtractedContent, config: SummaryConfig) -> str:
    """Build prompt for LLM summarization."""
    text = content.text
    
    # Redact PII if requested
    if config.redact_pii:
        text = redact_pii(text)
    
    # Truncate if too long (keep first 8000 chars for prompt)
    if len(text) > 8000:
        text = text[:8000] + "\n\n[Content truncated...]"
    
    schema = {
        "title": "string (3-7 words, inferred from content)",
        "date": "string (extracted date or 'Not specified')",
        "executive_summary": "array of 5-10 bullet point strings",
        "decisions": [
            {
                "decision": "string",
                "owner": "string or 'Unassigned'",
                "effective_date": "string or 'Not specified'"
            }
        ],
        "action_items": [
            {
                "owner": "string or 'Unassigned'",
                "task": "string",
                "deadline": "string or 'Not specified'",
                "status": "string or 'Not specified'"
            }
        ],
        "risks_blockers": [
            {
                "risk": "string",
                "severity": "string (Low/Med/High)",
                "owner": "string or 'Unassigned'",
                "mitigation": "string or 'None'"
            }
        ],
        "key_notes": "array of high-signal bullet point strings (max 10)",
        "metrics_dates": [
            {
                "item": "string",
                "value": "string",
                "notes": "string or 'None'"
            }
        ],
        "source_pages": "array of page numbers where key info was found (e.g., [1, 3, 5])"
    }
    
    return f"""You are a professional meeting/document summarizer. Extract ONLY the important information from the following content.

TASK:
Create a concise summary focusing on:
- Key decisions and approvals
- Action items with owners and deadlines
- Risks, blockers, and concerns
- Important metrics, dates, and milestones
- High-signal context (not fluff or boilerplate)

RULES:
- Do NOT include: attendance lists, generic introductions, thank-you messages, table of contents, page numbers
- Do NOT quote verbatim unless absolutely necessary
- If information is missing, use "Not specified" or "Unassigned" or "None"
- Be concise and action-oriented
- Deduplicate repeated information
- Do NOT hallucinate - if unclear, say "Not specified"

OUTPUT FORMAT:
Return ONLY valid JSON matching this schema:
{json.dumps(schema, indent=2)}

CONTENT:
<<<START>>>
{text}
<<<END>>>
""".strip()


def parse_model_json(raw: str) -> dict:
    """Parse JSON from model response with error recovery."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    
    # Extract first {...} block
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end+1]
    
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[WARN] JSON parse error: {e}")
        # Try to fix common issues
        import re
        fixed = re.sub(r',(\s*[}\]])', r'\1', raw)
        try:
            return json.loads(fixed)
        except:
            return {}


def summarize_pdf(input_path: Path, output_path: Path, config: SummaryConfig) -> SummaryResult:
    """
    Summarize a PDF file.
    
    Args:
        input_path: Path to input PDF
        output_path: Path for output summary PDF
        config: Summary configuration
    
    Returns:
        SummaryResult with success status and details
    """
    try:
        # Extract text from PDF
        print(f"[INFO] Extracting text from {input_path}...")
        content = extract_text_from_pdf(input_path, use_ocr=config.use_ocr, max_pages=config.max_pages)
        
        if not content.text.strip():
            return SummaryResult(
                success=False,
                error="No text extracted from PDF",
                pages_total=len(content.pages),
                extraction_method=content.extraction_method
            )
        
        print(f"[INFO] Extracted {len(content.text)} characters using {content.extraction_method}")
        
        # Build prompt and call LLM
        print("[INFO] Generating summary with AI...")
        prompt = build_summary_prompt(content, config)
        
        try:
            raw_response = call_ollama(prompt, temperature=config.temperature, num_predict=config.num_predict)
        except RuntimeError as e:
            return SummaryResult(
                success=False,
                error=f"LLM call failed: {e}",
                pages_total=len(content.pages),
                extraction_method=content.extraction_method
            )
        
        if not raw_response:
            return SummaryResult(
                success=False,
                error="Empty response from LLM",
                pages_total=len(content.pages),
                extraction_method=content.extraction_method
            )
        
        # Parse response
        summary_data = parse_model_json(raw_response)
        if not summary_data:
            return SummaryResult(
                success=False,
                error="Failed to parse LLM response as JSON",
                pages_total=len(content.pages),
                extraction_method=content.extraction_method
            )
        
        # Add source page references if available
        if not summary_data.get("source_pages"):
            # Try to infer from content pages (simplified)
            summary_data["source_pages"] = list(range(1, min(len(content.pages) + 1, 10)))
        
        # Render PDF
        print(f"[INFO] Rendering summary PDF to {output_path}...")
        try:
            render_summary_pdf(output_path, summary_data, source_pdf_name=input_path.name)
        except Exception as e:
            return SummaryResult(
                success=False,
                error=f"Failed to render PDF: {e}",
                pages_total=len(content.pages),
                extraction_method=content.extraction_method
            )
        
        return SummaryResult(
            success=True,
            output_path=output_path,
            pages_processed=len(content.pages),
            pages_total=len(content.pages),
            extraction_method=content.extraction_method,
            summary_stats={
                'decisions_count': len(summary_data.get('decisions', [])),
                'action_items_count': len(summary_data.get('action_items', [])),
                'risks_count': len(summary_data.get('risks_blockers', []))
            }
        )
    
    except Exception as e:
        return SummaryResult(
            success=False,
            error=f"Unexpected error: {str(e)}",
            pages_total=0
        )
