"""Check if all dependencies for PDF summarization are available."""
import sys
from typing import List, Tuple


def check_dependencies() -> Tuple[bool, List[str]]:
    """
    Check if all required and optional dependencies are available.
    
    Returns:
        Tuple of (all_required_available, list of missing/warnings)
    """
    missing = []
    warnings = []
    
    # Required dependencies
    try:
        import reportlab
    except ImportError:
        missing.append("reportlab (required for PDF generation)")
    
    try:
        import requests
    except ImportError:
        missing.append("requests (required for Ollama API)")
    
    try:
        from dotenv import load_dotenv
    except ImportError:
        missing.append("python-dotenv (required for config)")
    
    # Optional but recommended for PDF extraction
    try:
        from pypdf import PdfReader
    except ImportError:
        warnings.append("pypdf (optional but recommended for PDF text extraction)")
    
    # Optional OCR dependencies
    try:
        import pytesseract
    except ImportError:
        warnings.append("pytesseract (optional, needed for scanned PDFs)")
    
    try:
        from pdf2image import convert_from_path
    except ImportError:
        warnings.append("pdf2image (optional, needed for scanned PDFs)")
    
    try:
        from PIL import Image
    except ImportError:
        warnings.append("Pillow (optional, needed for OCR)")
    
    # Check Ollama availability
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        import requests
        response = requests.get(f"{base_url}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            if not models:
                warnings.append("Ollama is running but no models are installed (run: ollama pull qwen2.5:3b)")
        else:
            warnings.append(f"Ollama API returned status {response.status_code}")
    except requests.exceptions.ConnectionError:
        warnings.append("Ollama is not running or not accessible (required for summarization)")
    except Exception as e:
        warnings.append(f"Could not verify Ollama: {e}")
    
    all_required = len(missing) == 0
    return all_required, missing + warnings


if __name__ == "__main__":
    print("Checking PDF summarization dependencies...\n")
    all_ok, issues = check_dependencies()
    
    if all_ok and not issues:
        print("✅ All dependencies are available!")
    elif all_ok:
        print("✅ All required dependencies are available.")
        print("\n⚠️  Optional dependencies/warnings:")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print("❌ Missing required dependencies:")
        for issue in issues:
            if "required" in issue.lower() or "reportlab" in issue.lower() or "requests" in issue.lower() or "python-dotenv" in issue.lower():
                print(f"   - {issue}")
        
        if any("optional" in i.lower() for i in issues):
            print("\n⚠️  Optional dependencies/warnings:")
            for issue in issues:
                if "optional" in issue.lower():
                    print(f"   - {issue}")
        
        print("\n💡 Install missing dependencies with:")
        print("   pip install reportlab requests python-dotenv")
        print("   pip install pypdf pytesseract pdf2image Pillow  # Optional for OCR")
        sys.exit(1)
