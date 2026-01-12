# Ollama Setup Instructions

## Quick Setup

1. **Download Ollama**
   - Go to: https://ollama.com/download/windows
   - Download and run `OllamaSetup.exe`
   - Follow the installation wizard

2. **Run Setup Script**
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup_ollama.ps1
   ```

   This script will:
   - Verify Ollama is installed
   - Start Ollama service
   - Install the required model (llama3.1:8b)
   - Test the connection

3. **Manual Setup (if script doesn't work)**

   Open a new PowerShell window and run:
   ```powershell
   # Start Ollama
   ollama serve
   
   # In another window, install the model
   ollama pull llama3.1:8b
   
   # Verify it works
   ollama list
   ```

## Verify Ollama is Running

After installation, Ollama should start automatically. To verify:

```powershell
# Check if Ollama is running
ollama list

# If it says "connection refused", start it:
ollama serve
```

## Testing PDF Generation

Once Ollama is installed and running:

1. Upload a meeting through the web interface
2. The system will automatically:
   - Create the transcript
   - Generate the AI-powered PDF report
   - Email the PDF to all participants
   - Upload the PDF to connected apps (Google Drive, Dropbox, Box)

## Troubleshooting

### "Connection refused" error
- **Solution**: Start Ollama by running `ollama serve` in a terminal

### "Model not found" error
- **Solution**: Install the model: `ollama pull llama3.1:8b`

### PDF not being created
- Check that Ollama is running: `ollama list`
- Check the terminal output for error messages
- Verify the model is installed: `ollama list` should show `llama3.1:8b`

### PDF created but not emailed
- The system only emails PDFs (no TXT files)
- If PDF creation fails, no email is sent (by design)
- Check terminal logs for PDF creation status

## Environment Variables (Optional)

You can customize Ollama settings in your `.env` file:

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

## Notes

- Ollama needs to be running whenever you want to generate PDFs
- The first PDF generation may be slower as the model loads
- PDFs are only created if Ollama is running and the model is installed
- No TXT files are sent - only PDFs
