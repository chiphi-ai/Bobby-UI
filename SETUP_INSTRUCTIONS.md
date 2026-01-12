# Setup Instructions for Team Members

This guide will walk you through setting up the Phi AI Meeting Transcription System on your local machine.

## Quick Start Checklist

- [ ] Install Python 3.8+
- [ ] Install Git
- [ ] Install FFmpeg
- [ ] Clone the repository
- [ ] Create virtual environment
- [ ] Install Python dependencies
- [ ] Create `.env` file with API keys
- [ ] Run the application

## Detailed Setup Steps

### Step 1: Install Prerequisites

#### Python
- Download from https://www.python.org/downloads/
- **Important:** Check "Add Python to PATH" during installation
- Verify installation: `python --version` (should show 3.8 or higher)

#### Git
- Download from https://git-scm.com/downloads
- Verify installation: `git --version`

#### FFmpeg
**Windows:**
1. Download from https://www.gyan.dev/ffmpeg/builds/
2. Extract the zip file
3. Add the `bin` folder to your system PATH, OR
4. Copy `ffmpeg.exe` and `ffprobe.exe` to the project directory

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt-get install ffmpeg
```

Verify installation: `ffmpeg -version`

### Step 2: Clone the Repository

```bash
git clone <repository-url>
cd Dio
```

Replace `<repository-url>` with the actual Git repository URL provided by your team lead.

### Step 3: Set Up Python Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If you get an execution policy error, run:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

**macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` in your terminal prompt when activated.

### Step 4: Install Python Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This may take several minutes as it installs PyTorch and other large packages.

### Step 5: Create Environment Variables File

Create a file named `.env` in the project root directory (same folder as `web_app.py`).

**Get API Keys:**
- Contact your team lead for the AssemblyAI API key
- For cloud storage (optional), see `OAUTH_SETUP_GUIDE.md`

**Example `.env` file:**
```env
ASSEMBLYAI_API_KEY=your_key_here
```

**Important:** 
- Never share your `.env` file
- Never commit it to git
- The `.env` file is already in `.gitignore`

### Step 6: Verify File Structure

Make sure these directories exist (they will be created automatically, but verify):
- `input/` - Contains `users.csv` and `emails.csv`
- `enroll/` - For enrollment audio files
- `output/` - For generated transcripts
- `templates/` - HTML templates
- `static/` - CSS, images, etc.

### Step 7: Run the Application

```bash
python web_app.py
```

You should see output like:
```
 * Running on http://127.0.0.1:5000
```

Open your browser and go to: `http://localhost:5000`

### Step 8: Create Your Account

1. Click "Sign Up" on the homepage
2. Fill in your information:
   - First Name
   - Last Name
   - Email
   - Password
3. Click "Sign Up"
4. You'll be redirected to set a username
5. Complete voice enrollment (record at least 30 seconds)

## First-Time User Guide

### 1. Voice Enrollment (Required)

Before you can record meetings, you need to enroll your voice:

1. Go to "Voice Enrollment" page
2. Click "Start Recording" and speak for at least 30 seconds
3. Click "Stop Recording"
4. Listen to the playback
5. Click "Submit" to save

**Tip:** Speak clearly and naturally. The system uses this to identify you in meetings.

### 2. Record Your First Meeting

1. Go to "Record Meeting" page
2. Select an organization and your role
3. Click "Start Recording" or upload an audio file
4. Wait for processing (this may take a few minutes)
5. View your transcript!

## Troubleshooting

### "Python not found" or "python: command not found"

- Make sure Python is installed and added to PATH
- Try `python3` instead of `python` (macOS/Linux)
- Restart your terminal after installing Python

### "pip: command not found"

- Make sure Python is installed correctly
- Try `python -m pip` instead of just `pip`

### "FFmpeg not found"

- Verify FFmpeg is installed: `ffmpeg -version`
- If on Windows, make sure `ffmpeg.exe` is in PATH or in the project folder
- Restart terminal after adding to PATH

### "ModuleNotFoundError" or "No module named X"

- Make sure virtual environment is activated (you should see `(venv)`)
- Run `pip install -r requirements.txt` again
- Make sure you're in the project directory

### "AssemblyAI API Key not found"

- Check that `.env` file exists in the project root
- Check that it contains `ASSEMBLYAI_API_KEY=your_key`
- Make sure there are no spaces around the `=` sign
- Restart the application after creating/editing `.env`

### Application won't start

- Check the console for error messages
- Make sure port 5000 is not already in use
- Try a different port: Edit `web_app.py` and change `port=5000` to `port=5001`

### Can't access the website

- Make sure the application is running (check terminal)
- Try `http://127.0.0.1:5000` instead of `localhost:5000`
- Check your firewall settings

## Getting Help

1. **Check the console output** - Error messages usually appear there
2. **Review this document** - Make sure you completed all steps
3. **Check `OAUTH_SETUP_GUIDE.md`** - For cloud storage issues
4. **Contact your team lead** - For API keys and repository access

## Next Steps

Once you're set up:
- Complete voice enrollment
- Try recording a test meeting
- Explore the organization management features
- Set up cloud storage (optional) - see `OAUTH_SETUP_GUIDE.md`

## Important Notes

- **Never commit sensitive files** - `.env`, `users.csv`, etc. are in `.gitignore`
- **Keep your API keys secure** - Don't share them or commit them to git
- **Regular updates** - Pull latest changes regularly: `git pull`
- **Virtual environment** - Always activate it before running the app

Happy transcribing! 🎤📝
