# PDF Summarization Feature

This module extends the `meeting_pdf_summarizer` to support **PDF-to-PDF summarization**. It takes any PDF (meeting notes, slides, transcripts, reports) and creates a concise "Important Summary" version containing only key information.

## Features

- **PDF Text Extraction**: Extracts text from PDFs using `pypdf` (with OCR fallback via `pytesseract` for scanned PDFs)
- **AI-Powered Summarization**: Uses Ollama to identify and extract:
  - Key decisions and approvals
  - Action items with owners and deadlines
  - Risks, blockers, and concerns
  - Important metrics, dates, and milestones
  - High-signal context (excludes fluff, boilerplate, attendance lists)
- **Professional PDF Output**: Generates clean, structured summary PDFs with:
  - Executive summary (5-10 bullets)
  - Decisions table
  - Action items table (Owner | Task | Deadline | Status)
  - Risks & blockers
  - Key notes
  - Metrics & dates
  - Source page references
- **PII Redaction**: Optional flag to remove emails/phone numbers from summaries
- **OCR Support**: Handles scanned PDFs when text extraction fails

## Installation

Install additional dependencies:

```bash
pip install pypdf pytesseract pdf2image Pillow
```

**Note**: For OCR support, you also need:
- **Windows**: Install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) and [Poppler](https://github.com/oschwartz10612/poppler-windows/releases)
- **Mac**: `brew install tesseract poppler`
- **Linux**: `sudo apt-get install tesseract-ocr poppler-utils`

## Usage

### Command Line

```bash
# Basic usage
python -m meeting_pdf_summarizer summarize --in meeting.pdf --out summary.pdf

# With OCR for scanned PDFs
python -m meeting_pdf_summarizer summarize --in scanned.pdf --out summary.pdf --ocr

# With PII redaction
python -m meeting_pdf_summarizer summarize --in meeting.pdf --out summary.pdf --redact

# Limit pages
python -m meeting_pdf_summarizer summarize --in long_doc.pdf --out summary.pdf --max-pages 10
```

### Python API

```python
from pathlib import Path
from meeting_pdf_summarizer import prepare_pdf_for_sending

# Create summary PDF
original_pdf = Path("meeting_report.pdf")
summary_pdf = prepare_pdf_for_sending(original_pdf)

if summary_pdf:
    print(f"Summary created: {summary_pdf}")
```

### Advanced Usage

```python
from pathlib import Path
from meeting_pdf_summarizer import summarize_pdf, SummaryConfig

config = SummaryConfig(
    mode="important",  # "important", "full", or "brief"
    use_ocr=False,
    redact_pii=True,
    max_pages=None
)

result = summarize_pdf(
    input_path=Path("meeting.pdf"),
    output_path=Path("summary.pdf"),
    config=config
)

if result.success:
    print(f"Summary created: {result.output_path}")
    print(f"Pages processed: {result.pages_processed}/{result.pages_total}")
```

## Integration with Web App

The summarization is automatically integrated into the meeting workflow:

1. Meeting is recorded and transcribed
2. Named transcript is created
3. Full meeting report PDF is generated
4. **Summary PDF is created** (important info only)
5. Summary PDF is emailed and uploaded to connected apps

The summary PDF is what recipients receive - a concise version focusing on decisions, action items, risks, and key metrics.

## Module Structure

```
meeting_pdf_summarizer/
├── pdf_extract.py      # PDF text extraction (pypdf + OCR)
├── importance.py       # Importance scoring and filtering
├── summarize_pdf.py     # LLM-based summarization
├── render_pdf.py       # PDF generation (reportlab)
├── redact.py           # PII redaction utilities
├── types.py            # Data structures (SummaryConfig, SummaryResult)
├── cli.py              # Command-line interface
├── __init__.py         # Package exports (prepare_pdf_for_sending)
└── __main__.py         # Module entry point
```

## Requirements

- Python 3.8+
- Ollama running locally with `llama3.1:8b` model (or configured via `.env`)
- See `requirements.txt` for Python dependencies

## Troubleshooting

### "No text extracted from PDF"
- Try `--ocr` flag for scanned PDFs
- Ensure PDF is not password-protected
- Check if PDF contains actual text (not just images)

### "Ollama API call failed"
- Ensure Ollama is running: `ollama list`
- Check `OLLAMA_URL` in `.env` (default: `http://localhost:11434`)
- Verify model is installed: `ollama pull llama3.1:8b`

### OCR not working
- Install Tesseract OCR and Poppler
- On Windows, add Tesseract to PATH or set `TESSDATA_PREFIX` environment variable
- Verify: `pytesseract.image_to_string()` works

## Example Output

The summary PDF includes:

- **Title & Date**: Inferred from content
- **Executive Summary**: 5-10 key bullet points
- **Key Decisions**: Decision | Owner | Effective Date
- **Action Items**: Owner | Task | Deadline | Status (table format)
- **Risks & Blockers**: Risk | Severity | Owner | Mitigation
- **Key Notes**: High-signal context only
- **Metrics & Dates**: Important numbers and milestones
- **Source References**: Page numbers where key info was found

All sections exclude fluff, boilerplate, attendance lists, and generic content.
