# Ollama Setup Script for Windows
Write-Host "=== Ollama Setup for Meeting PDF Summarizer ===" -ForegroundColor Cyan
Write-Host ""

# Check if Ollama is installed
Write-Host "Step 1: Checking if Ollama is installed..." -ForegroundColor Yellow
$ollamaCheck = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCheck) {
    Write-Host "  [OK] Ollama is installed!" -ForegroundColor Green
    $version = ollama --version 2>&1
    Write-Host "  Version: $version" -ForegroundColor Gray
} else {
    Write-Host "  [ERROR] Ollama is NOT installed" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Please install Ollama:" -ForegroundColor Yellow
    Write-Host "  1. Go to: https://ollama.com/download/windows" -ForegroundColor Cyan
    Write-Host "  2. Download and run OllamaSetup.exe" -ForegroundColor Cyan
    Write-Host "  3. After installation, run this script again" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Opening download page..." -ForegroundColor Yellow
    Start-Process "https://ollama.com/download/windows"
    exit 1
}

# Check if Ollama is running
Write-Host ""
Write-Host "Step 2: Checking if Ollama is running..." -ForegroundColor Yellow
try {
    $null = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -Method GET -TimeoutSec 3 -ErrorAction Stop
    Write-Host "  [OK] Ollama is running!" -ForegroundColor Green
} catch {
    Write-Host "  [WARNING] Ollama is not running" -ForegroundColor Yellow
    Write-Host "  Starting Ollama..." -ForegroundColor Yellow
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -Method GET -TimeoutSec 5 -ErrorAction Stop
        Write-Host "  [OK] Ollama started!" -ForegroundColor Green
    } catch {
        Write-Host "  [ERROR] Could not start Ollama" -ForegroundColor Red
        Write-Host "  Please run manually: ollama serve" -ForegroundColor Yellow
        exit 1
    }
}

# Check for model
Write-Host ""
Write-Host "Step 3: Checking for model qwen2.5:3b..." -ForegroundColor Yellow
$models = ollama list 2>&1
if ($models -match "qwen2.5:3b") {
    Write-Host "  [OK] Model qwen2.5:3b is installed!" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Model qwen2.5:3b is not installed" -ForegroundColor Yellow
    Write-Host "  Installing model (this may take a few minutes)..." -ForegroundColor Yellow
    ollama pull qwen2.5:3b
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Model installed!" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to install model" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host "Ollama is ready! PDFs will now be generated." -ForegroundColor Green
Write-Host ""
