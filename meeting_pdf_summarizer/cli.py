"""Command-line interface for PDF summarization."""
import argparse
from pathlib import Path
from typing import Optional

from .summarize_pdf import summarize_pdf
from .summary_types import SummaryConfig
from .render_pdf import render_summary_pdf
import json


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Summarize PDF documents to extract important information"
    )
    parser.add_argument(
        "command",
        choices=["summarize"],
        help="Command to execute"
    )
    parser.add_argument(
        "--in", "--input",
        dest="input_path",
        required=True,
        type=Path,
        help="Path to input PDF file"
    )
    parser.add_argument(
        "--out", "--output",
        dest="output_path",
        required=True,
        type=Path,
        help="Path for output summary PDF"
    )
    parser.add_argument(
        "--mode",
        choices=["important", "full", "brief"],
        default="important",
        help="Summary mode (default: important)"
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Use OCR for scanned PDFs (requires pytesseract and pdf2image)"
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        help="Redact PII (emails, phone numbers) from summary"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Maximum number of pages to process"
    )
    
    args = parser.parse_args()
    
    if args.command == "summarize":
        config = SummaryConfig(
            mode=args.mode,
            use_ocr=args.ocr,
            redact_pii=args.redact,
            max_pages=args.max_pages
        )
        
        result = summarize_pdf(args.input_path, args.output_path, config)
        
        if result.success:
            print(f"✅ Summary created: {result.output_path}")
            print(f"   Pages processed: {result.pages_processed}/{result.pages_total}")
            print(f"   Extraction method: {result.extraction_method}")
            if result.summary_stats:
                print(f"   Decisions: {result.summary_stats.get('decisions_count', 0)}")
                print(f"   Action items: {result.summary_stats.get('action_items_count', 0)}")
                print(f"   Risks: {result.summary_stats.get('risks_count', 0)}")
        else:
            print(f"❌ Error: {result.error}")
            exit(1)


if __name__ == "__main__":
    main()
