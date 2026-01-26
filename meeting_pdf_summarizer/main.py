# Meeting PDF Summarizer
# Generates structured meeting summaries from transcripts using Ollama AI

import argparse
import json
import os
import re
from typing import Dict, List, Tuple

import requests
from dotenv import load_dotenv

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.units import inch



SPEAKER_COLON_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_\- ]{0,60})\s*:\s*(.+?)\s*$")
SPEAKER_BRACKET_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.+?)\s*$")
SPEAKER_DASH_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_\- ]{0,60})\s-\s(.+?)\s*$")
SPEAKER_EM_DASH_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_\- ]{0,60})\s\u2014\s(.+?)\s*$")
SPEAKER_EN_DASH_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_\- ]{0,60})\s\u2013\s(.+?)\s*$")
TIMESTAMP_RE = re.compile(r"^\s*(?:\[\s*)?\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:\s*\])?\s*")

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _strip_timestamp(line: str) -> str:
    line = TIMESTAMP_RE.sub("", line, count=1)
    return re.sub(r"^\s*[-\u2013\u2014]\s*", "", line)

def _normalize_speaker(speaker: str) -> str:
    return re.sub(r"\s+", " ", speaker.strip())

def _parse_json_transcript(text: str) -> List[Dict[str, str]]:
    raw = (text or "").strip()
    if not raw or not (raw.startswith("{") or raw.startswith("[")):
        return []

    def as_turns(items):
        turns: List[Dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            speaker = item.get("speaker") or item.get("name") or item.get("spk")
            content = item.get("text") or item.get("utterance") or item.get("content")
            if speaker and content:
                turns.append({"speaker": str(speaker).strip(), "text": str(content).strip()})
        return turns

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            turns = as_turns(data)
            if turns:
                return turns
        if isinstance(data, dict):
            for key in ("utterances", "turns", "segments", "results"):
                if isinstance(data.get(key), list):
                    turns = as_turns(data.get(key))
                    if turns:
                        return turns
    except json.JSONDecodeError:
        pass

    turns: List[Dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            speaker = item.get("speaker") or item.get("name") or item.get("spk")
            content = item.get("text") or item.get("utterance") or item.get("content")
            if speaker and content:
                turns.append({"speaker": str(speaker).strip(), "text": str(content).strip()})
    return turns

def _log_turns(turns: List[Dict[str, str]]) -> None:
    print(f"[DEBUG] Speaker turns parsed: {len(turns)}")
    speakers = sorted({t.get('speaker', '').strip() for t in turns if t.get('speaker')})
    print(f"[DEBUG] Unique speakers: {speakers}")
    for t in turns[:3]:
        preview = (t.get("text") or "")[:80]
        print(f"[DEBUG] Turn preview: {t.get('speaker', 'Unknown')}: {preview}")

def parse_transcript(text: str) -> List[Dict[str, str]]:
    lines = text.splitlines()
    print(f"[DEBUG] Transcript lines read: {len(lines)}")

    json_turns = _parse_json_transcript(text)
    if json_turns:
        _log_turns(json_turns)
        return json_turns

    turns: List[Dict[str, str]] = []
    found_label = False

    for raw_line in lines:
        line = _strip_timestamp(raw_line).strip()
        if not line:
            continue

        match = (
            SPEAKER_COLON_RE.match(line)
            or SPEAKER_BRACKET_RE.match(line)
            or SPEAKER_EM_DASH_RE.match(line)
            or SPEAKER_EN_DASH_RE.match(line)
            or SPEAKER_DASH_RE.match(line)
        )
        if match:
            found_label = True
            speaker = _normalize_speaker(match.group(1))
            content = match.group(2).strip()
            turns.append({"speaker": speaker, "text": content})
        else:
            if turns:
                turns[-1]["text"] = f"{turns[-1]['text']} {line}".strip()

    if not turns:
        preview = lines[:50]
        if preview:
            print("[WARN] No speaker turns parsed from transcript lines. Raw preview:")
            for pline in preview:
                print(f"[WARN] {pline}")
        else:
            print("[WARN] Transcript is empty or whitespace-only.")

    if not found_label:
        preview = lines[:30]
        if preview:
            print("[WARN] No speaker labels detected. Falling back to single-speaker parsing.")
            for pline in preview:
                print(f"[WARN] {pline}")

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paragraphs:
            compact = " ".join([ln.strip() for ln in lines if ln.strip()]).strip()
            paragraphs = [compact] if compact else ["(no transcript content)"]
        turns = [{"speaker": "Speaker", "text": p} for p in paragraphs]

    if not turns:
        turns = [{"speaker": "Speaker", "text": "(no transcript content)"}]

    _log_turns(turns)
    return turns

def run_debug_parse_samples() -> None:
    samples = {
        "colon": "Alice: Hello team\nBob: Hi Alice",
        "brackets": "[Charlie] Here's an update on the timeline.",
        "dash": "Dana - We should ship Friday",
        "em_dash": "Erin \u2014 The build is green",
        "speaker_labels": "SPEAKER 1: Hello\nSpeaker 2: Hi\nUnknown Speaker 1: Not sure",
        "timestamps": "00:01:23 Alice: Starting now\n[00:01:25] Bob: Sounds good",
        "continuations": "Fiona: First point\nstill same speaker\nGabe: Next item",
        "json_list": '[{"speaker":"Hal","text":"Line one"},{"speaker":"Ivy","text":"Line two"}]',
        "json_dict": '{"utterances":[{"speaker":"Jan","text":"Welcome"},{"speaker":"Kim","text":"Thanks"}]}',
        "no_labels": "This is a paragraph.\n\nThis is another paragraph without labels.",
    }

    for name, text in samples.items():
        print(f"\n[DEBUG] Sample: {name}")
        turns = parse_transcript(text)
        print(f"[DEBUG] Parsed turns: {turns}")

def load_roles(path: str) -> Tuple[List[Dict], Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["roles"], cfg.get("name_to_role", {})

def escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")



def truncate_transcript(text: str, max_chars: int = 24000) -> str:
    """
    Truncate transcript to fit within model context limits.
    Keeps beginning and end, removes middle if too long.
    24000 chars ≈ 6000 tokens, safe for 3b models with 4k context.
    """
    if len(text) <= max_chars:
        return text
    
    # Keep 40% from start, 40% from end, mark truncation in middle
    keep_chars = max_chars - 100  # Leave room for truncation message
    start_chars = int(keep_chars * 0.4)
    end_chars = int(keep_chars * 0.4)
    
    start_text = text[:start_chars]
    end_text = text[-end_chars:]
    
    # Find clean break points (end of utterance/line)
    start_break = start_text.rfind('\n')
    if start_break > start_chars * 0.8:
        start_text = start_text[:start_break]
    
    end_break = end_text.find('\n')
    if end_break > 0 and end_break < end_chars * 0.2:
        end_text = end_text[end_break + 1:]
    
    truncated_chars = len(text) - len(start_text) - len(end_text)
    print(f"[INFO] Transcript truncated: {len(text)} -> {len(start_text) + len(end_text)} chars ({truncated_chars} chars removed from middle)")
    
    return f"{start_text}\n\n[... {truncated_chars} characters omitted for length ...]\n\n{end_text}"


def call_ollama(prompt: str) -> str:
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
            "temperature": 0.2,
            "num_predict": 1500,  # Reduced for smaller model
            "num_ctx": 4096,      # Explicit context window
            "num_batch": 256,     # Smaller batch size to reduce memory
        }
    }

    print(f"[INFO] Calling Ollama with model: {model}")
    r = requests.post(url, json=payload, timeout=180)  # 3 min timeout for smaller model
    r.raise_for_status()
    data = r.json()
    return data.get("response", "")

def parse_model_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        print("[ERROR] Empty response from Ollama")
        return {}
    
    # salvage first {...} if the model adds anything
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end+1]
    
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse JSON from Ollama response: {e}")
        print(f"[DEBUG] Error at position: {e.pos if hasattr(e, 'pos') else 'unknown'}")
        print(f"[DEBUG] Response preview (first 500 chars): {raw[:500]}")
        if len(raw) > 500:
            print(f"[DEBUG] Response preview (last 500 chars): {raw[-500:]}")
        
        # Try to fix common JSON issues
        import re
        # Remove trailing commas before closing braces/brackets
        fixed = re.sub(r',(\s*[}\]])', r'\1', raw)
        
        # Try to complete incomplete JSON by closing open structures
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        
        # If JSON appears incomplete, try to close it
        if open_braces > 0 or open_brackets > 0:
            # Remove incomplete last entry and close structures
            # Find the last complete entry before the error
            if hasattr(e, 'pos') and e.pos and e.pos < len(raw):
                # Try to find a safe cut-off point
                cut_pos = e.pos
                # Go back to find a complete entry
                for i in range(cut_pos - 1, max(0, cut_pos - 200), -1):
                    if raw[i] in ['}', ']', ',']:
                        # Check if this looks like a complete entry
                        test_json = raw[:i+1]
                        # Close any open structures
                        test_json += '}' * (test_json.count('{') - test_json.count('}'))
                        test_json += ']' * (test_json.count('[') - test_json.count(']'))
                        try:
                            return json.loads(test_json)
                        except:
                            continue
        
        # Try to fix unescaped newlines and quotes in strings
        # This is complex, so we'll try a simpler approach first
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e2:
            # Last resort: try to extract what we can
            print(f"[ERROR] Could not fix JSON. Second error: {e2}")
            print("[WARNING] Attempting to extract partial JSON...")
            
            # Try to find and parse the largest valid JSON substring
            for end_pos in range(len(raw), max(0, len(raw) - 500), -1):
                test_str = raw[:end_pos]
                # Close open structures
                test_str += '}' * (test_str.count('{') - test_str.count('}'))
                test_str += ']' * (test_str.count('[') - test_str.count(']'))
                try:
                    result = json.loads(test_str)
                    print(f"[WARNING] Successfully parsed partial JSON (first {end_pos} chars)")
                    return result
                except:
                    continue
            
            print("[ERROR] Could not extract any valid JSON. Returning empty dict.")
            return {}


def build_prompt(transcript_text: str) -> str:
    # Truncate long transcripts to prevent memory issues with smaller models
    transcript_text = truncate_transcript(transcript_text, max_chars=24000)
    
    schema = {
        "meeting_title": "string",
        "date": "string",
        "one_line_purpose": "string",
        "executive_snapshot": "string",
        "key_decisions_made": [
            {"decision": "string", "owner": "string", "effective_date": "string"}
        ],
        "action_items_next_steps": [
            {"action": "string", "owner": "string", "due": "string", "dependencies": "string"}
        ],
        "open_questions_unresolved": [
            {"question_or_issue": "string", "owner": "string", "target_resolution_date": "string"}
        ],
        "risks_concerns_constraints": [
            {"risk": "string", "severity": "Low|Med|High", "owner": "string", "mitigation_next_step": "string"}
        ],
        "important_context_rationale": [
            {"tradeoff_or_constraint": "string", "rationale": "string"}
        ],
        "key_metrics_dates_milestones": [
            {"item": "string", "value_or_date": "string", "notes": "string"}
        ],
        "follow_up_cadence": {
            "next_check_in": "string",
            "what_will_be_covered": "string"
        }
    }

    return f"""
You are a meeting summarizer. Extract key information from this transcript.

TASK:
Generate a business meeting summary in the exact JSON structure below. Be concise and action-oriented.

RULES:
- Do not quote the transcript verbatim.
- If a section has no content, use "None" or empty list [].
- Extract deadlines and owners when stated; if missing, use "Not specified" or "Unassigned".
- Do NOT include attendance/participants.

OUTPUT:
Return ONLY valid JSON matching this schema:
{json.dumps(schema, indent=2)}

Field rules:
- meeting_title: 3–7 words inferred from transcript.
- date: "Not specified" if not mentioned.
- executive_snapshot: 2–4 sentences.
- Empty lists: use [].
- Empty strings: use "None" (except date which uses "Not specified").

TRANSCRIPT:
{transcript_text}
""".strip()





def generate_pdf(output_path: str, content: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Meeting Summary",
    )

    story = []
    story.append(Paragraph("<b>Meeting Summary</b>", styles["Title"]))
    story.append(Spacer(1, 12))

    for line in content.splitlines():
        if not line.strip():
            story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(escape(line), styles["BodyText"]))

    doc.build(story)

def add_heading(story, styles, text, level=2):
    style = "Heading2" if level == 2 else "Heading3"
    story.append(Paragraph(f"<b>{escape(text)}</b>", styles[style]))
    story.append(Spacer(1, 8))

def add_bullets(story, styles, bullets):
    if not bullets:
        story.append(Paragraph("- (none)", styles["BodyText"]))
        story.append(Spacer(1, 6))
        return
    for b in bullets:
        story.append(Paragraph(f"- {escape(b)}", styles["BodyText"]))
    story.append(Spacer(1, 10))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to transcript .txt")
    parser.add_argument("--roles", default="roles.json", help="Path to roles.json")
    parser.add_argument("--output", required=True, help="Path to output PDF")
    parser.add_argument("--upload-date", default=None, help="Upload/processed date (ISO format) to override AI-extracted date")
    parser.add_argument("--source-organizations", default=None, help="Comma-separated list of source organizations")
    parser.add_argument("--debug-parse", action="store_true", help="Run transcript parser samples and exit")
    args = parser.parse_args()

    if args.debug_parse:
        run_debug_parse_samples()
        return

    # Read transcript first
    transcript = read_text(args.input)
    
    # Parse transcript to validate format
    turns = parse_transcript(transcript)
    if not turns:
        print("[WARN] Transcript parsing produced no turns; using fallback single-speaker entry.")
        turns = [{"speaker": "Speaker", "text": "(no transcript content)"}]

    # Load roles
    roles, name_to_role = load_roles(args.roles)
    
    # Build prompt and call Ollama
    prompt = build_prompt(transcript)
    raw = call_ollama(prompt)
    
    if not raw:
        raise RuntimeError("Ollama returned empty response. Check if Ollama is running and the model is available.")

    print("[DEBUG] Raw response length:", len(raw))
    print("[DEBUG] Raw response preview:", raw[:300])
    
    data = parse_model_json(raw)
    
    if not data:
        raise RuntimeError("Failed to parse JSON from Ollama response. Check the debug output above for details.")

    print("[DEBUG] Parsed data keys:", list(data.keys()) if isinstance(data, dict) else "Not a dict")

    # Override date with upload date if provided
    if args.upload_date:
        try:
            from datetime import datetime
            # Parse ISO format and format nicely
            dt = datetime.fromisoformat(args.upload_date.replace('Z', '+00:00'))
            data["date"] = dt.strftime("%Y-%m-%d")
            print(f"[INFO] Using upload date: {data['date']}")
        except Exception as e:
            print(f"[WARN] Could not parse upload date '{args.upload_date}': {e}, using AI-extracted date")

    # Add source organizations if provided
    if args.source_organizations:
        orgs_list = [org.strip() for org in args.source_organizations.split(",") if org.strip()]
        data["source_organizations"] = orgs_list
        print(f"[INFO] Source organizations: {', '.join(orgs_list)}")

    # Generate PDF
    generate_pdf_from_data(args.output, data)
    print(f"PDF created: {args.output}")

def generate_pdf_from_data(output_path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Meeting Summary"
    )

    story = []

    def h(text: str):
        story.append(Paragraph(escape(text), styles["Heading2"]))
        story.append(Spacer(1, 6))

    def p(text: str):
        story.append(Paragraph(escape(text), styles["BodyText"]))
        story.append(Spacer(1, 6))

    def none_line():
        p("None")

    def get_str(key: str, default: str = "None") -> str:
        val = data.get(key, default)
        if val is None:
            return default
        val = str(val).strip()
        return val if val else default

    def list_or_empty(key: str):
        val = data.get(key, [])
        return val if isinstance(val, list) else []

    # --- EXACT HEADERS IN ORDER ---

    # Source Organizations Header (if available)
    source_orgs = data.get("source_organizations", [])
    if source_orgs and isinstance(source_orgs, list) and len(source_orgs) > 0:
        h("SOURCE ORGANIZATIONS:")
        orgs_text = ", ".join([escape(org) for org in source_orgs if org])
        p(orgs_text)
        story.append(Spacer(1, 8))

    h("MEETING TITLE:")
    p(get_str("meeting_title", "None"))

    h("DATE:")
    p(get_str("date", "Not specified"))

    h("ONE-LINE PURPOSE:")
    p(get_str("one_line_purpose", "None"))

    h("EXECUTIVE SNAPSHOT:")
    p(get_str("executive_snapshot", "None"))

    h("KEY DECISIONS MADE:")
    decisions = list_or_empty("key_decisions_made")
    if not decisions:
        none_line()
    else:
        for d in decisions:
            decision = (d.get("decision") or "").strip() if isinstance(d, dict) else ""
            owner = (d.get("owner") or "Unassigned").strip() if isinstance(d, dict) else "Unassigned"
            eff = (d.get("effective_date") or "Not specified").strip() if isinstance(d, dict) else "Not specified"
            # Format as a single indented block for better readability
            story.append(Paragraph(f"• {escape(decision or 'None')}", styles["BodyText"]))
            if owner and owner != "Unassigned":
                story.append(Paragraph(f"    Owner: {escape(owner)}", styles["BodyText"]))
            if eff and eff != "Not specified":
                story.append(Paragraph(f"    Effective: {escape(eff)}", styles["BodyText"]))
            story.append(Spacer(1, 8))

    h("ACTION ITEMS & NEXT STEPS:")
    actions = list_or_empty("action_items_next_steps")
    if not actions:
        none_line()
    else:
        # Use table format with Owner, Task, and Due columns
        table_data = [[Paragraph(escape("Owner"), styles["BodyText"]), 
                       Paragraph(escape("Task"), styles["BodyText"]), 
                       Paragraph(escape("Due"), styles["BodyText"])]]
        for a in actions:
            if not isinstance(a, dict):
                continue
            action = (a.get("action") or "").strip()
            owner = (a.get("owner") or "Unassigned").strip()
            due = (a.get("due") or "Not specified").strip()
            deps = (a.get("dependencies") or "None").strip()
            
            # Include dependencies in task if present
            task_text = action or "None"
            if deps and deps != "None" and deps:
                task_text = f"{task_text} (Dependencies: {deps})"
            
            # Use Paragraph objects for proper text wrapping
            table_data.append([
                Paragraph(escape(owner), styles["BodyText"]),
                Paragraph(escape(task_text), styles["BodyText"]),
                Paragraph(escape(due), styles["BodyText"])
            ])
        
        # Only create table if we have data rows (beyond header)
        if len(table_data) > 1:
            # Calculate available width: letter (8.5") - left margin (0.75") - right margin (0.75") = 7"
            # Use 1.2" for Owner, 4.3" for Task, 1.5" for Due to prevent overlap
            t = Table(table_data, colWidths=[1.2*inch, 4.3*inch, 1.5*inch], repeatRows=1)
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("WORDWRAP", (0, 0), (-1, -1), True),  # Enable word wrapping
            ]))
            story.append(t)
            story.append(Spacer(1, 10))
        else:
            none_line()

    h("OPEN QUESTIONS & UNRESOLVED ISSUES:")
    oq = list_or_empty("open_questions_unresolved")
    if not oq:
        none_line()
    else:
        for q in oq:
            issue = (q.get("question_or_issue") or "").strip() if isinstance(q, dict) else ""
            owner = (q.get("owner") or "Unassigned").strip() if isinstance(q, dict) else "Unassigned"
            target = (q.get("target_resolution_date") or "Not specified").strip() if isinstance(q, dict) else "Not specified"
            story.append(Paragraph(f"• {escape(issue or 'None')}", styles["BodyText"]))
            if owner and owner != "Unassigned":
                story.append(Paragraph(f"    Owner: {escape(owner)}", styles["BodyText"]))
            if target and target != "Not specified":
                story.append(Paragraph(f"    Target: {escape(target)}", styles["BodyText"]))
            story.append(Spacer(1, 8))

    h("RISKS, CONCERNS, & CONSTRAINTS:")
    risks = list_or_empty("risks_concerns_constraints")
    if not risks:
        none_line()
    else:
        for r in risks:
            risk = (r.get("risk") or "").strip() if isinstance(r, dict) else ""
            severity = (r.get("severity") or "Med").strip() if isinstance(r, dict) else "Med"
            owner = (r.get("owner") or "Unassigned").strip() if isinstance(r, dict) else "Unassigned"
            mit = (r.get("mitigation_next_step") or "None").strip() if isinstance(r, dict) else "None"
            story.append(Paragraph(f"• {escape(risk or 'None')}", styles["BodyText"]))
            story.append(Paragraph(f"    Severity: {escape(severity or 'Med')}", styles["BodyText"]))
            if owner and owner != "Unassigned":
                story.append(Paragraph(f"    Owner: {escape(owner)}", styles["BodyText"]))
            if mit and mit != "None":
                story.append(Paragraph(f"    Mitigation: {escape(mit)}", styles["BodyText"]))
            story.append(Spacer(1, 8))

    h("IMPORTANT CONTEXT & RATIONALE:")
    ctx = list_or_empty("important_context_rationale")
    if not ctx:
        none_line()
    else:
        for c in ctx:
            tradeoff = (c.get("tradeoff_or_constraint") or "").strip() if isinstance(c, dict) else ""
            rationale = (c.get("rationale") or "").strip() if isinstance(c, dict) else ""
            story.append(Paragraph(f"• {escape(tradeoff or 'None')}", styles["BodyText"]))
            if rationale and rationale != "None":
                story.append(Paragraph(f"    Rationale: {escape(rationale)}", styles["BodyText"]))
            story.append(Spacer(1, 8))

    h("KEY METRICS, DATES, & MILESTONES MENTIONED:")
    kms = list_or_empty("key_metrics_dates_milestones")
    if not kms:
        none_line()
    else:
        for m in kms:
            item = (m.get("item") or "").strip() if isinstance(m, dict) else ""
            val = (m.get("value_or_date") or "").strip() if isinstance(m, dict) else ""
            notes = (m.get("notes") or "None").strip() if isinstance(m, dict) else "None"
            story.append(Paragraph(f"• {escape(item or 'None')}", styles["BodyText"]))
            if val and val != "Not specified":
                story.append(Paragraph(f"    Value/Date: {escape(val)}", styles["BodyText"]))
            if notes and notes != "None":
                story.append(Paragraph(f"    Notes: {escape(notes)}", styles["BodyText"]))
            story.append(Spacer(1, 8))

    h("FOLLOW-UP CADENCE:")
    cadence = data.get("follow_up_cadence", {})
    if not isinstance(cadence, dict):
        cadence = {}

    next_check_in = (cadence.get("next_check_in") or "Not specified").strip()
    covered = (cadence.get("what_will_be_covered") or "None").strip()

    p(f"- Next check-in date/time (if stated; otherwise “Not specified”): {next_check_in or 'Not specified'}")
    p(f"- What will be covered: {covered or 'None'}")

    doc.build(story)




if __name__ == "__main__":
    main()
