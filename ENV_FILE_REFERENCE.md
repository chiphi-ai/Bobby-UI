# .env File Reference

This document shows exactly what your `.env` file should look like and what each variable does.

## File Location

Create a file named `.env` in the project root directory (same folder as `web_app.py`).

**Important:**
- File must be named exactly `.env` (with the dot at the beginning)
- No file extension (not `.env.txt` or `.env.txt`)
- Must be in the root directory: `C:\Users\bjones25\Documents\Dio\.env`

## Complete .env File Template

```env
# ============================================
# REQUIRED: AssemblyAI API Key
# ============================================
ASSEMBLYAI_API_KEY=your_assemblyai_api_key_here

# ============================================
# OPTIONAL: Flask Secret Key
# ============================================
FLASK_SECRET_KEY=

# ============================================
# OPTIONAL: Encryption Key for OAuth Tokens
# ============================================
ENCRYPTION_KEY=

# ============================================
# OPTIONAL: Email Configuration (SMTP)
# ============================================
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password_here

# ============================================
# OPTIONAL: Dropbox OAuth
# ============================================
DROPBOX_CLIENT_ID=your_dropbox_client_id
DROPBOX_CLIENT_SECRET=your_dropbox_client_secret

# ============================================
# OPTIONAL: Google Drive OAuth
# ============================================
GOOGLE_DRIVE_CLIENT_ID=your_google_client_id
GOOGLE_DRIVE_CLIENT_SECRET=your_google_client_secret

# ============================================
# OPTIONAL: Box OAuth
# ============================================
BOX_CLIENT_ID=your_box_client_id
BOX_CLIENT_SECRET=your_box_client_secret

# ============================================
# OPTIONAL: Ollama Configuration (for AI-powered meeting summaries)
# ============================================
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

## Variable Descriptions

### REQUIRED Variables

#### `ASSEMBLYAI_API_KEY`
- **Required:** YES
- **Purpose:** API key for AssemblyAI transcription service
- **Where to get it:** https://www.assemblyai.com/app/account
- **Format:** A long string of letters and numbers
- **Example:** `ASSEMBLYAI_API_KEY=98e0a0993e5b4849810df8cfced7aca4`

**Without this, transcription will NOT work!**

### OPTIONAL Variables

#### `FLASK_SECRET_KEY`
- **Required:** NO (auto-generated if not set)
- **Purpose:** Used for Flask session encryption
- **Format:** Random hex string (64 characters)
- **Generate:** `python -c "import secrets; print(secrets.token_hex(32))"`
- **Example:** `FLASK_SECRET_KEY=a1b2c3d4e5f6...`

#### `ENCRYPTION_KEY`
- **Required:** NO (auto-generated if not set)
- **Purpose:** Encrypts OAuth tokens stored in database
- **Format:** Random hex string (64 characters)
- **Generate:** `python -c "import secrets; print(secrets.token_hex(32))"`
- **Example:** `ENCRYPTION_KEY=f6e5d4c3b2a1...`

#### `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`
- **Required:** NO (only if you want email notifications)
- **Purpose:** SMTP server configuration for sending emails
- **For Gmail:**
  - `SMTP_HOST=smtp.gmail.com`
  - `SMTP_PORT=587`
  - `SMTP_USER=your_email@gmail.com`
  - `SMTP_PASS=your_app_password` (NOT your regular password!)
  - Get App Password: https://myaccount.google.com/apppasswords

#### `DROPBOX_CLIENT_ID`, `DROPBOX_CLIENT_SECRET`
- **Required:** NO (only if you want Dropbox integration)
- **Purpose:** OAuth credentials for Dropbox
- **Where to get:** https://www.dropbox.com/developers/apps
- **See:** `OAUTH_SETUP_GUIDE.md` for detailed setup

#### `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`
- **Required:** NO (only if you want Google Drive integration)
- **Purpose:** OAuth credentials for Google Drive
- **Where to get:** https://console.cloud.google.com/
- **See:** `OAUTH_SETUP_GUIDE.md` for detailed setup

#### `BOX_CLIENT_ID`, `BOX_CLIENT_SECRET`
- **Required:** NO (only if you want Box integration)
- **Purpose:** OAuth credentials for Box
- **Where to get:** https://developer.box.com/
- **See:** `OAUTH_SETUP_GUIDE.md` for detailed setup

#### `OLLAMA_URL`, `OLLAMA_MODEL`
- **Required:** NO (only if you want AI-powered meeting summaries)
- **Purpose:** Configuration for Ollama AI service used to generate structured meeting summaries
- **OLLAMA_URL:** URL where Ollama is running (default: `http://localhost:11434`)
- **OLLAMA_MODEL:** Model to use for summarization (default: `llama3.1:8b`)
- **Setup:**
  1. Install Ollama from https://ollama.ai/
  2. Pull the model: `ollama pull llama3.1:8b`
  3. Make sure Ollama is running (it should start automatically)
  4. Verify: `ollama list` should show your model
- **Note:** Without Ollama, meeting reports will use heuristic-based summaries instead of AI-powered ones

## Example .env File (Minimal - Just Required)

```env
ASSEMBLYAI_API_KEY=98e0a0993e5b4849810df8cfced7aca4
```

## Example .env File (Full - All Features)

```env
ASSEMBLYAI_API_KEY=98e0a0993e5b4849810df8cfced7aca4
FLASK_SECRET_KEY=a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456
ENCRYPTION_KEY=f6e5d4c3b2a1987654321098765432109876543210fedcba9876543210fedcba98
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=mail@phiartificialintelligence.com
SMTP_PASS=ejpntssypayiioel
DROPBOX_CLIENT_ID=dhpdciuin7211aokorgxcucx1xcayuig
DROPBOX_CLIENT_SECRET=your_dropbox_secret_here
GOOGLE_DRIVE_CLIENT_ID=your_google_client_id
GOOGLE_DRIVE_CLIENT_SECRET=your_google_secret_here
BOX_CLIENT_ID=dhpdciuin7211aokorgxcucx1xcayuig
BOX_CLIENT_SECRET=your_box_secret_here
```

## Common Errors and Fixes

### Error: "ASSEMBLYAI_API_KEY not found"

**Problem:** The `.env` file is missing or the key is not set correctly.

**Fix:**
1. Make sure `.env` file exists in the project root
2. Check the file name is exactly `.env` (not `.env.txt`)
3. Make sure the line is: `ASSEMBLYAI_API_KEY=your_key_here`
4. No spaces around the `=` sign
5. No quotes around the value (unless the value itself needs quotes)
6. Restart the application after creating/editing `.env`

### Error: "ModuleNotFoundError: No module named 'dotenv'"

**Problem:** `python-dotenv` package is not installed.

**Fix:**
```bash
pip install python-dotenv
```

### Error: Environment variables not loading

**Problem:** The `.env` file format is incorrect.

**Fix:**
- Make sure each variable is on its own line
- Format: `VARIABLE_NAME=value`
- No spaces before or after `=`
- No quotes unless necessary
- Lines starting with `#` are comments (ignored)

### Error: "Invalid API key" or "Authentication failed"

**Problem:** The API key value is incorrect.

**Fix:**
- Check for typos in the API key
- Make sure you copied the entire key
- Verify the key is active in your AssemblyAI account
- Make sure there are no extra spaces or characters

## File Format Rules

✅ **DO:**
- Use format: `VARIABLE_NAME=value`
- Put each variable on its own line
- Use comments with `#` to document
- Keep the file in the project root directory

❌ **DON'T:**
- Don't use spaces around `=`
- Don't use quotes unless necessary
- Don't add file extensions (`.env.txt`)
- Don't commit `.env` to git (it's in `.gitignore`)

## Verifying Your .env File

### Check if file exists:
```bash
# Windows PowerShell
Test-Path .env

# Windows CMD
if exist .env echo File exists

# macOS/Linux
ls -la .env
```

### Check if variables are loading:
```python
# In Python console (after activating venv)
from dotenv import load_dotenv
import os
load_dotenv()
print(os.getenv("ASSEMBLYAI_API_KEY"))
```

If it prints `None`, the variable is not set correctly.

## Security Notes

⚠️ **IMPORTANT:**
- Never commit `.env` to git (it's already in `.gitignore`)
- Never share your `.env` file
- Never post API keys publicly
- Keep your `.env` file secure and private
- Rotate keys if they're ever exposed

## Need Help?

If you're still having issues:
1. Check the console output for specific error messages
2. Verify the `.env` file is in the correct location
3. Make sure you restarted the application after creating/editing `.env`
4. Check that `python-dotenv` is installed: `pip list | findstr dotenv`
