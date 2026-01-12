"""Type definitions for PDF summarization."""
from dataclasses import dataclass
from typing import Optional, List, Dict
from pathlib import Path


@dataclass
class SummaryConfig:
    """Configuration for PDF summarization."""
    mode: str = "important"  # "important", "full", "brief"
    use_ocr: bool = False  # Enable OCR for scanned PDFs
    redact_pii: bool = False  # Remove emails/phone numbers
    max_pages: Optional[int] = None  # Limit pages to process
    temperature: float = 0.2  # LLM temperature
    num_predict: int = 2000  # Max tokens to generate


@dataclass
class SummaryResult:
    """Result of PDF summarization."""
    success: bool
    output_path: Optional[Path] = None
    error: Optional[str] = None
    pages_processed: int = 0
    pages_total: int = 0
    extraction_method: str = "text"  # "text" or "ocr"
    summary_stats: Optional[Dict] = None  # Optional stats about the summary
