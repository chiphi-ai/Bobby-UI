"""Verify PDF summarization setup is complete and working."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    """Run verification checks."""
    print("=" * 70)
    print("PDF Summarization Setup Verification")
    print("=" * 70)
    print()
    
    all_checks_passed = True
    
    # Check 1: Python version
    print("1. Checking Python version...")
    if sys.version_info >= (3, 8):
        print(f"   [OK] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    else:
        print(f"   [FAIL] Python {sys.version_info.major}.{sys.version_info.minor} (requires 3.8+)")
        all_checks_passed = False
    print()
    
    # Check 2: Required dependencies
    print("2. Checking required dependencies...")
    required = {
        "reportlab": "PDF generation",
        "requests": "Ollama API calls",
        "dotenv": "python-dotenv (environment variables)",
    }
    
    missing_required = []
    for module, desc in required.items():
        try:
            if module == "dotenv":
                __import__("dotenv")
            else:
                __import__(module)
            print(f"   [OK] {module} - {desc}")
        except ImportError:
            print(f"   [FAIL] {module} - {desc} (MISSING)")
            missing_required.append(module)
            all_checks_passed = False
    
    if missing_required:
        print(f"\n   [TIP] Install with: pip install {' '.join(missing_required)}")
    print()
    
    # Check 3: Optional dependencies
    print("3. Checking optional dependencies...")
    optional = {
        "pypdf": "PDF text extraction (recommended)",
        "pytesseract": "OCR for scanned PDFs",
        "pdf2image": "OCR support",
        "PIL": "Pillow (OCR support)",
    }
    
    missing_optional = []
    for module, desc in optional.items():
        try:
            if module == "PIL":
                __import__("PIL")
            else:
                __import__(module)
            print(f"   [OK] {module} - {desc}")
        except ImportError:
            print(f"   [WARN] {module} - {desc} (optional)")
            missing_optional.append(module)
    
    if missing_optional:
        print(f"\n   [TIP] Install optional deps: pip install {' '.join(missing_optional)}")
    print()
    
    # Check 4: Module imports
    print("4. Checking PDF summarization modules...")
    try:
        from meeting_pdf_summarizer import prepare_pdf_for_sending, summarize_pdf, SummaryConfig
        print("   [OK] All modules import successfully")
    except ImportError as e:
        print(f"   [FAIL] Import failed: {e}")
        all_checks_passed = False
    except Exception as e:
        print(f"   [FAIL] Unexpected error: {e}")
        all_checks_passed = False
    print()
    
    # Check 5: Ollama connection
    print("5. Checking Ollama connection...")
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        
        import requests
        response = requests.get(f"{base_url}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            if models:
                model_names = [m.get("name", "unknown") for m in models]
                print(f"   [OK] Ollama is running at {base_url}")
                print(f"   [OK] Models available: {', '.join(model_names[:3])}")
                if "llama3.1:8b" in str(models):
                    print(f"   [OK] Required model 'llama3.1:8b' is installed")
                else:
                    print(f"   [WARN] Model 'llama3.1:8b' not found (install with: ollama pull llama3.1:8b)")
            else:
                print(f"   [WARN] Ollama is running but no models installed")
                print(f"   💡 Install model: ollama pull llama3.1:8b")
        else:
            print(f"   [WARN] Ollama API returned status {response.status_code}")
    except requests.exceptions.ConnectionError:
        print(f"   [FAIL] Ollama is not running or not accessible")
        print(f"   [TIP] Start Ollama or check OLLAMA_URL in .env")
        all_checks_passed = False
    except Exception as e:
        print(f"   [WARN] Could not verify Ollama: {e}")
    print()
    
    # Check 6: Test PDF availability
    print("6. Checking for test PDFs...")
    output_dir = ROOT / "output"
    test_pdfs = list(output_dir.glob("*_meeting_report.pdf"))
    if test_pdfs:
        print(f"   [OK] Found {len(test_pdfs)} test PDF(s) in output/")
        print(f"      Example: {test_pdfs[0].name}")
    else:
        print(f"   [WARN] No test PDFs found (upload a meeting to generate one)")
    print()
    
    # Summary
    print("=" * 70)
    if all_checks_passed:
        print("[SUCCESS] All required checks passed! PDF summarization is ready to use.")
        print()
        print("Next steps:")
        print("  1. Upload a meeting through the web interface")
        print("  2. The system will automatically create a summary PDF")
        print("  3. Recipients will receive the concise summary version")
    else:
        print("[FAIL] Some checks failed. Please fix the issues above.")
        print()
        print("Quick fix commands:")
        if missing_required:
            print(f"  pip install {' '.join(missing_required)}")
        if missing_optional:
            print(f"  pip install {' '.join(missing_optional)}  # Optional")
    print("=" * 70)
    
    return 0 if all_checks_passed else 1

if __name__ == "__main__":
    sys.exit(main())
