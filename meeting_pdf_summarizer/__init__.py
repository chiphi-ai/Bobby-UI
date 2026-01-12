"""PDF summarization package."""
from pathlib import Path
from typing import Optional
import sys

# Check dependencies on import
try:
    from .summarize_pdf import summarize_pdf
    from .summary_types import SummaryConfig, SummaryResult
except ImportError as e:
    # Provide helpful error message
    print(f"[WARN] Could not import PDF summarization modules: {e}")
    print(f"       Install dependencies: pip install reportlab requests python-dotenv pypdf")
    # Don't fail completely, allow graceful degradation
    summarize_pdf = None
    SummaryConfig = None
    SummaryResult = None


def prepare_pdf_for_sending(original_pdf_path: Path, output_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Create a summarized version of a PDF for sending/sharing.
    
    This function takes a meeting PDF (could be slides, notes, transcript PDF, or report)
    and creates a concise "Important Summary" version containing only key information.
    
    Args:
        original_pdf_path: Path to the original PDF file
        output_dir: Directory for output (default: same as input)
    
    Returns:
        Path to the summary PDF, or None if summarization failed
    """
    if summarize_pdf is None:
        print(f"[ERROR] PDF summarization not available - dependencies missing")
        print(f"       Install: pip install reportlab requests python-dotenv pypdf")
        return None
    
    if not original_pdf_path.exists():
        print(f"[ERROR] PDF not found: {original_pdf_path}")
        return None
    
    # Determine output path
    if output_dir is None:
        output_dir = original_pdf_path.parent
    
    stem = original_pdf_path.stem
    # Use _summary suffix instead of replacing the entire name
    summary_path = output_dir / f"{stem}_summary.pdf"
    
    # Create config for important-only summary
    config = SummaryConfig(
        mode="important",
        use_ocr=False,  # Try text extraction first
        redact_pii=False,  # Don't redact by default
        max_pages=None  # Process all pages
    )
    
    # Summarize
    try:
        result = summarize_pdf(original_pdf_path, summary_path, config)
        
        if result.success and result.output_path and result.output_path.exists():
            file_size = result.output_path.stat().st_size
            if file_size > 0:
                print(f"[SUCCESS] Summary PDF created: {result.output_path} ({file_size} bytes)")
                return result.output_path
            else:
                print(f"[ERROR] Summary PDF is empty (0 bytes)")
                return None
        else:
            error_msg = result.error if result else "Unknown error"
            print(f"[ERROR] Summarization failed: {error_msg}")
            return None
    except Exception as e:
        print(f"[ERROR] Exception during summarization: {e}")
        import traceback
        traceback.print_exc()
        return None


__all__ = ["summarize_pdf", "SummaryConfig", "SummaryResult", "prepare_pdf_for_sending"]
