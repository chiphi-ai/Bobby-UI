# How to Clone and Set Up This Repository

## Step 1: Install Git

### Windows
1. Download Git from: https://git-scm.com/download/win
2. Run the installer (use default settings)
3. Open PowerShell or Command Prompt

### Mac
- Git is usually pre-installed. Check by running: `git --version`
- If not installed, use Homebrew: `brew install git`

### Linux
```bash
# Ubuntu/Debian
sudo apt install git

# CentOS/RHEL
sudo yum install git
```

## Step 2: Clone the Repository

1. **Get the repository URL:**
   - Go to the GitHub repository page
   - Click the green "Code" button
   - Copy the HTTPS URL (looks like: `https://github.com/username/repo-name.git`)

2. **Open Terminal/PowerShell/Command Prompt**

3. **Navigate to where you want the project:**
   ```bash
   cd Documents
   # or wherever you want to save it
   ```

4. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/REPO_NAME.git
   ```
   
   Replace with the actual URL. Example:
   ```bash
   git clone https://github.com/james/meeting-pdf-summarizer.git
   ```

5. **Enter the project folder:**
   ```bash
   cd REPO_NAME
   # or
   cd meeting-pdf-summarizer
   ```

## Step 3: Install Python Dependencies

```bash
pip install -r requirements.txt
```

If you get permission errors, use:
```bash
pip install --user -r requirements.txt
```

## Step 4: Install and Set Up Ollama

1. **Download Ollama:**
   - Go to: https://ollama.ai/
   - Download and install for your operating system

2. **Pull the AI model:**
   ```bash
   ollama pull llama3.1:8b
   ```
   
   This downloads the model (about 4.7GB). It may take a few minutes.

3. **Verify Ollama is running:**
   ```bash
   ollama list
   ```
   
   You should see `llama3.1:8b` in the list.

## Step 5: Test the Installation

Run a test with the sample transcript:

```bash
python main.py --input transcripts/tiny.txt --output out/test.pdf
```

If successful, you should see:
```
PDF created: out/test.pdf
```

## Troubleshooting

### "git: command not found"
- Make sure Git is installed (Step 1)
- Restart your terminal after installing Git
- On Windows, you may need to use "Git Bash" instead of Command Prompt

### "python: command not found"
- Make sure Python is installed: https://www.python.org/downloads/
- On some systems, use `python3` instead of `python`
- On Windows, you may need to add Python to your PATH

### "pip: command not found"
- Try `python -m pip` instead of just `pip`
- Or `python3 -m pip` on Mac/Linux

### Ollama connection errors
- Make sure Ollama is running (it should start automatically)
- Check if the model is installed: `ollama list`
- Try pulling the model again: `ollama pull llama3.1:8b`

## Quick Reference

```bash
# Clone repository
git clone https://github.com/USERNAME/REPO.git
cd REPO

# Install dependencies
pip install -r requirements.txt

# Set up Ollama
ollama pull llama3.1:8b

# Run the script
python main.py --input transcripts/tiny.txt --output out/test.pdf
```
