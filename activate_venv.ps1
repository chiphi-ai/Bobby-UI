# Activate virtual environment and verify setup
Write-Host "Activating virtual environment..." -ForegroundColor Green
.\.venv\Scripts\Activate.ps1

Write-Host "`nVerifying setup..." -ForegroundColor Yellow
python -c "import flask; print('✅ Flask installed')"
python -c "from dotenv import load_dotenv; from pathlib import Path; import os; load_dotenv(Path('.env'), override=True); key = os.getenv('ASSEMBLYAI_API_KEY'); print('✅ .env file loaded'); print('✅ API Key length:', len(key) if key else 0)"

Write-Host "`n✅ Ready to run: python web_app.py" -ForegroundColor Green
