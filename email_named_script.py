import argparse
import csv
import json
import os
import re
import ssl
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# Load .env file if python-dotenv is available
# Use utf-8-sig to handle BOM if present
try:
    from dotenv import load_dotenv
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        # Read directly to handle BOM, then use load_dotenv
        with open(env_file, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line.startswith(('SMTP_HOST=', 'SMTP_PORT=', 'SMTP_USER=', 'SMTP_PASS=')):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
    load_dotenv(env_file, override=True)
except ImportError:
    pass  # python-dotenv not installed, will use environment variables only
except Exception:
    pass  # If file read fails, fall back to load_dotenv only

# -------------------------
# Paths
# -------------------------
DB_CSV = Path("input") / "emails.csv"

# -------------------------
# Email config
# -------------------------
FROM_NAME = "Phi AI"
SUBJECT_TEMPLATE = "{stem} meeting transcript (named)"
BODY_TEMPLATE = """Hi {first},

Here's the named script from our {stem} meeting.

Best,
Phi AI Team
"""

ATTACH_SCRIPT = True
DRY_RUN = False         # set True for testing, False to actually send
SLEEP_SECONDS = 1.5

# -------------------------
# SMTP env vars
# -------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()

ALLOWED = set(["first", "last", "email"])

def die(msg: str) -> None:
    raise SystemExit(f"\nERROR: {msg}\n")

def norm_key(first: str, last: str) -> str:
    first = re.sub(r"\s+", "", (first or "").strip().lower())
    last = re.sub(r"\s+", "", (last or "").strip().lower())
    return f"{first},{last}"

def read_db(path: Path) -> dict:
    if not path.exists():
        die(f"Missing CSV: {path} (expected headers: first,last,email)")
    with open(path, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            die("CSV has no headers.")
        headers = [h.strip().lower() for h in r.fieldnames]
        if any(h not in ALLOWED for h in headers):
            pass
        needed = {"first", "last", "email"}
        if not needed.issubset(set(headers)):
            die("CSV must have headers exactly: first,last,email")

        people = {}
        for row in r:
            first = (row.get("first") or "").strip()
            last = (row.get("last") or "").strip()
            email = (row.get("email") or "").strip()
            if first and last and email:
                people[norm_key(first, last)] = {"first": first, "last": last, "email": email}
        return people

def speakers_stats(named_json_path: Path) -> dict:
    """
    Reads output/<stem>_named_script.json and returns:
      key -> {seconds, words}
    Assumes each row has start/end/text/speaker_name (your identify_speakers.py already writes start/end/score/text).
    """
    data = json.loads(named_json_path.read_text(encoding="utf-8"))
    stats = {}
    for r in data:
        name = (r.get("speaker_name") or "").strip().lower()
        name = re.sub(r"\(\d+\)", "", name)  # Remove (2), (3) etc.
        name = re.sub(r"\s+", "", name)  # Remove all whitespace to match enrollment format (username)
        if name == "unknown":
            continue  # Skip unknown speakers
        txt = (r.get("text") or "").strip()
        if not name or not txt:
            continue

        start = float(r.get("start", 0.0))
        end = float(r.get("end", 0.0))
        dur = max(0.0, end - start)

        words = len(re.findall(r"\b\w+\b", txt))
        if name not in stats:
            stats[name] = {"seconds": 0.0, "words": 0}
        stats[name]["seconds"] += dur
        stats[name]["words"] += words
    return stats

def create_pdf(script_json_path: Path, people: dict, output_pdf_path: Path) -> bool:
    """Create PDF with format: First Name Last Name: what they said"""
    if not PDF_AVAILABLE:
        return False
    
    try:
        data = json.loads(script_json_path.read_text(encoding="utf-8"))
        doc = SimpleDocTemplate(str(output_pdf_path), pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            textColor='#1d1d1f',
            spaceAfter=20,
            fontName='Helvetica-Bold'
        )
        story.append(Paragraph("Meeting Transcript", title_style))
        story.append(Spacer(1, 0.2*inch))
        
        # Content
        body_style = ParagraphStyle(
            'CustomBody',
            parent=styles['Normal'],
            fontSize=11,
            textColor='#1d1d1f',
            leading=16,
            spaceAfter=12,
            fontName='Helvetica'
        )
        
        speaker_style = ParagraphStyle(
            'CustomSpeaker',
            parent=styles['Normal'],
            fontSize=12,
            textColor='#007AFF',
            leading=16,
            spaceAfter=4,
            fontName='Helvetica-Bold'
        )
        
        for r in data:
            speaker_name = r.get('speaker_name', 'Unknown')
            text = r.get('text', '').strip()
            
            if not text or speaker_name == 'Unknown':
                continue
            
            # Remove any (2), (3) etc. patterns first
            speaker_name_clean = re.sub(r"\(\d+\)", "", speaker_name).strip()
            
            # Convert username to "First Last" format using people dict
            # The people dict maps username -> user data (or old format "first,last" -> user data)
            display_name = None
            if speaker_name_clean.lower() in people:
                user_data = people[speaker_name_clean.lower()]
                if isinstance(user_data, dict) and "first" in user_data and "last" in user_data:
                    display_name = f"{user_data['first']} {user_data['last']}"
            
            if display_name:
                speaker_name = display_name
            elif "," in speaker_name_clean:
                # Fallback: old format "first,last"
                parts = speaker_name_clean.split(",")
                if len(parts) == 2:
                    first = parts[0].strip().capitalize()
                    last = parts[1].strip().capitalize()
                    speaker_name = f"{first} {last}"
                else:
                    speaker_name = speaker_name_clean.capitalize()
            else:
                # Preserve multi-word capitalization for custom speaker labels.
                # If the string is all-lowercase, title-case it; otherwise keep as provided.
                if speaker_name_clean and speaker_name_clean == speaker_name_clean.lower():
                    speaker_name = speaker_name_clean.title()
                else:
                    speaker_name = speaker_name_clean
            
            # Format: "First Name Last Name: what they said"
            speaker_para = Paragraph(f"<b>{speaker_name}:</b>", speaker_style)
            text_para = Paragraph(text.replace('\n', '<br/>'), body_style)
            
            story.append(speaker_para)
            story.append(text_para)
            story.append(Spacer(1, 0.15*inch))
        
        doc.build(story)
        return True
    except Exception as e:
        print(f"Error creating PDF: {e}")
        return False

def build_message(to_first: str, to_email: str, stem: str, script_text: str, pdf_path: Path = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
    msg["To"] = to_email
    msg["Subject"] = SUBJECT_TEMPLATE.format(stem=stem)
    msg.set_content(BODY_TEMPLATE.format(first=to_first, stem=stem))

    # Attach PDF if available, otherwise attach text
    if pdf_path and pdf_path.exists():
        with open(pdf_path, 'rb') as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="pdf",
                filename=f"{stem}_transcript.pdf",
            )
    elif ATTACH_SCRIPT:
        msg.add_attachment(
            script_text.encode("utf-8"),
            maintype="text",
            subtype="plain",
            filename=f"{stem}_named_script.txt",
        )
    return msg

def require_env():
    missing = [k for k, v in {
        "SMTP_HOST": SMTP_HOST,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASS": SMTP_PASS
    }.items() if not v]
    if missing:
        die("Missing env vars: " + ", ".join(missing))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stem", required=True)
    parser.add_argument("--min-seconds", type=float, default=6.0)
    parser.add_argument("--min-words", type=int, default=20)
    args = parser.parse_args()

    require_env()
    people = read_db(DB_CSV)

    stem = args.stem
    named_txt = Path("output") / f"{stem}_named_script.txt"
    named_json = Path("output") / f"{stem}_named_script.json"

    if not named_txt.exists():
        die(f"Missing: {named_txt}")
    if not named_json.exists():
        die(f"Missing: {named_json} (identify_speakers.py should create it)")

    script_text = named_txt.read_text(encoding="utf-8", errors="replace")
    stats = speakers_stats(named_json)

    # Filter speakers by thresholds AND roster
    recipients = []
    for spk_key, st in stats.items():
        if st["seconds"] >= args.min_seconds and st["words"] >= args.min_words:
            # Normalize the key to match enrollment format (lowercase, no spaces)
            normalized_key = spk_key.lower().strip()
            normalized_key = re.sub(r"\s+", "", normalized_key)
            if normalized_key in people:
                recipients.append((people[normalized_key], st))
            else:
                # Debug: print unmatched speakers
                print(f"  ⚠️  Speaker '{spk_key}' (normalized: '{normalized_key}') not found in roster.")
                print(f"      Available keys: {list(people.keys())}")

    recipients.sort(key=lambda x: (x[0]["last"].lower(), x[0]["first"].lower()))

    print(f"Stem: {stem}")
    print(f"Roster size: {len(people)}")
    print(f"Detected speakers: {len(stats)}")
    print(f"Thresholds: >= {args.min_seconds}s AND >= {args.min_words} words")
    print(f"Recipients: {len(recipients)}")
    for r, st in recipients:
        print(f"  - {r['first']} {r['last']} <{r['email']}>  ({st['seconds']:.1f}s, {st['words']} words)")
    print(f"DRY_RUN={DRY_RUN}")

    if not recipients:
        print("No recipients meet thresholds.")
        return

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)

        # Create PDF transcript
        pdf_path = Path("output") / f"{stem}_transcript.pdf"
        pdf_created = False
        if PDF_AVAILABLE:
            pdf_created = create_pdf(named_json, people, pdf_path)
            if pdf_created:
                print(f"Created PDF: {pdf_path}")
            else:
                print("PDF creation failed, will attach text file instead")
        
        for i, (r, _) in enumerate(recipients, start=1):
            msg = build_message(r["first"], r["email"], stem, script_text, pdf_path if pdf_created else None)
            if DRY_RUN:
                print(f"[{i}/{len(recipients)}] WOULD SEND to {r['email']}")
            else:
                server.send_message(msg)
                print(f"[{i}/{len(recipients)}] Sent to {r['email']}")
            time.sleep(SLEEP_SECONDS)

    print("Done.")

if __name__ == "__main__":
    main()
