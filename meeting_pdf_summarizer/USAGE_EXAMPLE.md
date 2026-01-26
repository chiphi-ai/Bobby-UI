# PDF Summarization - Usage Examples

## Quick Start

### 1. Command Line Usage

```bash
# Summarize a meeting PDF (important info only)
python -m meeting_pdf_summarizer summarize --in meeting_report.pdf --out summary.pdf

# With OCR for scanned PDFs
python -m meeting_pdf_summarizer summarize --in scanned.pdf --out summary.pdf --ocr

# With PII redaction
python -m meeting_pdf_summarizer summarize --in meeting.pdf --out summary.pdf --redact
```

### 2. Python API Usage

```python
from pathlib import Path
from meeting_pdf_summarizer import prepare_pdf_for_sending

# Simple usage - creates summary automatically
original = Path("output/meeting_20260109_meeting_report.pdf")
summary = prepare_pdf_for_sending(original)

if summary:
    print(f"Summary created: {summary}")
    # Use summary for email/upload
```

### 3. Advanced Usage

```python
from pathlib import Path
from meeting_pdf_summarizer import summarize_pdf, SummaryConfig

config = SummaryConfig(
    mode="important",      # Focus on important info only
    use_ocr=False,         # Try text extraction first
    redact_pii=False,      # Don't redact by default
    max_pages=None         # Process all pages
)

result = summarize_pdf(
    input_path=Path("meeting_report.pdf"),
    output_path=Path("summary.pdf"),
    config=config
)

if result.success:
    print(f"✅ Summary: {result.output_path}")
    print(f"   Pages: {result.pages_processed}/{result.pages_total}")
    print(f"   Method: {result.extraction_method}")
    print(f"   Decisions: {result.summary_stats['decisions_count']}")
    print(f"   Actions: {result.summary_stats['action_items_count']}")
else:
    print(f"❌ Error: {result.error}")
```

## Integration with Web App

The summarization is **automatically integrated** into the meeting workflow:

1. Meeting recorded → transcribed → named transcript created
2. Full meeting report PDF generated (via `main.py`)
3. **Summary PDF created** (important info only) ← NEW
4. Summary PDF emailed to participants
5. Summary PDF uploaded to connected apps (Dropbox, Google Drive, Box)

**Recipients receive the concise summary PDF**, not the full report.

## What Gets Summarized

The summary PDF includes:

- ✅ **Executive Summary**: 5-10 key bullet points
- ✅ **Key Decisions**: Decision | Owner | Effective Date
- ✅ **Action Items**: Owner | Task | Deadline | Status (table)
- ✅ **Risks & Blockers**: Risk | Severity | Owner | Mitigation
- ✅ **Key Notes**: High-signal context only
- ✅ **Metrics & Dates**: Important numbers and milestones
- ✅ **Source References**: Page numbers for traceability

**Excludes**:
- ❌ Attendance lists
- ❌ Generic introductions/thank-yous
- ❌ Table of contents
- ❌ Page numbers
- ❌ Boilerplate text
- ❌ Repeated information

## Requirements

- Python 3.8+
- Ollama running with `qwen2.5:3b` model
- Dependencies: `pypdf`, `reportlab`, `requests`, `python-dotenv`
- Optional (for OCR): `pytesseract`, `pdf2image`, `Pillow`

## Troubleshooting

**"No text extracted"**: Try `--ocr` flag for scanned PDFs

**"Ollama API call failed"**: Ensure Ollama is running (`ollama list`)

**Import errors**: Make sure you're running from the project root, not inside `meeting_pdf_summarizer/`
