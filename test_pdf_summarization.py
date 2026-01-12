"""Test script for PDF summarization functionality."""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")
    try:
        from meeting_pdf_summarizer import prepare_pdf_for_sending, summarize_pdf, SummaryConfig
        print("✅ All imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False

def test_dependencies():
    """Test dependency checking."""
    print("\nTesting dependencies...")
    try:
        from meeting_pdf_summarizer.check_dependencies import check_dependencies
        all_ok, issues = check_dependencies()
        if all_ok:
            print("✅ All required dependencies available")
        else:
            print("❌ Missing dependencies:")
            for issue in issues:
                print(f"   - {issue}")
        return all_ok
    except Exception as e:
        print(f"⚠️  Could not check dependencies: {e}")
        return False

def test_pdf_summarization(pdf_path: Path):
    """Test PDF summarization with a real PDF."""
    print(f"\nTesting PDF summarization with: {pdf_path}")
    
    if not pdf_path.exists():
        print(f"❌ PDF not found: {pdf_path}")
        return False
    
    try:
        from meeting_pdf_summarizer import prepare_pdf_for_sending
        output_dir = ROOT / "output"
        summary_path = prepare_pdf_for_sending(pdf_path, output_dir=output_dir)
        
        if summary_path and summary_path.exists():
            size = summary_path.stat().st_size
            print(f"✅ Summary created: {summary_path.name} ({size} bytes)")
            return True
        else:
            print(f"❌ Summary creation failed")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("=" * 60)
    print("PDF Summarization Test Suite")
    print("=" * 60)
    
    # Test 1: Imports
    if not test_imports():
        print("\n❌ Import test failed. Install dependencies first.")
        return 1
    
    # Test 2: Dependencies
    if not test_dependencies():
        print("\n⚠️  Some dependencies missing, but continuing...")
    
    # Test 3: Find a test PDF
    output_dir = ROOT / "output"
    test_pdfs = list(output_dir.glob("*_meeting_report.pdf"))
    
    if test_pdfs:
        test_pdf = test_pdfs[0]
        print(f"\nFound test PDF: {test_pdf.name}")
        if test_pdf_summarization(test_pdf):
            print("\n✅ All tests passed!")
            return 0
        else:
            print("\n❌ PDF summarization test failed")
            return 1
    else:
        print(f"\n⚠️  No test PDFs found in {output_dir}")
        print("   Upload a meeting first to generate a test PDF")
        return 0

if __name__ == "__main__":
    sys.exit(main())
