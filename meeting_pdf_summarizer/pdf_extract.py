"""PDF text extraction with OCR fallback."""
import re
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass

try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


@dataclass
class ExtractedContent:
    """Extracted content from a PDF."""
    text: str
    pages: List[str]
    headings: List[str]
    metadata: Dict
    extraction_method: str = "text"


def extract_text_from_pdf(pdf_path: Path, use_ocr: bool = False, max_pages: Optional[int] = None) -> ExtractedContent:
    """
    Extract text from a PDF file.
    
    Args:
        pdf_path: Path to the PDF file
        use_ocr: If True, use OCR even if text extraction works
        max_pages: Maximum number of pages to process (None = all)
    
    Returns:
        ExtractedContent with text, pages, headings, and metadata
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    
    # Try text extraction first
    if PYPDF_AVAILABLE and not use_ocr:
        try:
            return _extract_with_pypdf(pdf_path, max_pages)
        except Exception as e:
            print(f"[WARN] Text extraction failed: {e}")
            if OCR_AVAILABLE:
                print("[INFO] Falling back to OCR...")
                use_ocr = True
            else:
                raise RuntimeError(f"Text extraction failed and OCR not available: {e}")
    
    # Use OCR if requested or if text extraction failed
    if use_ocr or not PYPDF_AVAILABLE:
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR requested but pytesseract/pdf2image not installed. Install with: pip install pytesseract pdf2image pillow")
        return _extract_with_ocr(pdf_path, max_pages)
    
    raise RuntimeError("No PDF extraction method available. Install pypdf or pytesseract/pdf2image.")


def _extract_with_pypdf(pdf_path: Path, max_pages: Optional[int] = None) -> ExtractedContent:
    """Extract text using pypdf."""
    reader = PdfReader(str(pdf_path))
    
    total_pages = len(reader.pages)
    pages_to_process = min(total_pages, max_pages) if max_pages else total_pages
    
    pages_text = []
    all_text = []
    headings = []
    
    for i in range(pages_to_process):
        page = reader.pages[i]
        page_text = page.extract_text()
        
        if page_text.strip():
            pages_text.append(page_text)
            all_text.append(page_text)
            
            # Extract potential headings (lines that are short and formatted differently)
            lines = page_text.split('\n')
            for line in lines[:20]:  # Check first 20 lines for headings
                line = line.strip()
                if line and len(line) < 100 and not line.endswith('.'):
                    # Likely a heading if it's short, capitalized, or has special formatting
                    if line.isupper() or (len(line.split()) <= 8 and line[0].isupper()):
                        headings.append(line)
    
    full_text = '\n\n'.join(all_text)
    
    # Extract metadata
    metadata = {}
    if reader.metadata:
        metadata = {
            'title': reader.metadata.get('/Title', ''),
            'author': reader.metadata.get('/Author', ''),
            'subject': reader.metadata.get('/Subject', ''),
            'creator': reader.metadata.get('/Creator', ''),
        }
    
    return ExtractedContent(
        text=full_text,
        pages=pages_text,
        headings=headings[:20],  # Limit headings
        metadata=metadata,
        extraction_method="text"
    )


def _extract_with_ocr(pdf_path: Path, max_pages: Optional[int] = None) -> ExtractedContent:
    """Extract text using OCR (pytesseract + pdf2image)."""
    try:
        images = convert_from_path(str(pdf_path))
    except Exception as e:
        raise RuntimeError(f"Failed to convert PDF to images: {e}. Make sure poppler is installed.")
    
    total_pages = len(images)
    pages_to_process = min(total_pages, max_pages) if max_pages else total_pages
    
    pages_text = []
    all_text = []
    headings = []
    
    for i, image in enumerate(images[:pages_to_process]):
        try:
            page_text = pytesseract.image_to_string(image)
            if page_text.strip():
                pages_text.append(page_text)
                all_text.append(page_text)
                
                # Extract potential headings
                lines = page_text.split('\n')
                for line in lines[:20]:
                    line = line.strip()
                    if line and len(line) < 100 and not line.endswith('.'):
                        if line.isupper() or (len(line.split()) <= 8 and line[0].isupper()):
                            headings.append(line)
        except Exception as e:
            print(f"[WARN] OCR failed for page {i+1}: {e}")
            pages_text.append("")
    
    full_text = '\n\n'.join(all_text)
    
    return ExtractedContent(
        text=full_text,
        pages=pages_text,
        headings=headings[:20],
        metadata={},
        extraction_method="ocr"
    )


def chunk_text(text: str, max_chunk_size: int = 3000) -> List[str]:
    """
    Split text into chunks for processing.
    Tries to break at sentence boundaries.
    """
    if len(text) <= max_chunk_size:
        return [text]
    
    chunks = []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    current_chunk = []
    current_size = 0
    
    for sentence in sentences:
        sentence_size = len(sentence)
        if current_size + sentence_size > max_chunk_size and current_chunk:
            chunks.append(' '.join(current_chunk))
            current_chunk = [sentence]
            current_size = sentence_size
        else:
            current_chunk.append(sentence)
            current_size += sentence_size + 1
    
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    
    return chunks
