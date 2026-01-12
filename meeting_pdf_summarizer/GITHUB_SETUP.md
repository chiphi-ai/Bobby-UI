# GitHub Setup Guide

Follow these steps to upload your project to GitHub and share it with your friend.

## Step 1: Install Git (if not already installed)

1. Download Git from: https://git-scm.com/download/win
2. Run the installer with default settings
3. Restart your terminal/PowerShell after installation

## Step 2: Create a GitHub Account and Repository

1. Go to https://github.com and sign up (or log in)
2. Click the "+" icon in the top right → "New repository"
3. Name it (e.g., `meeting-pdf-summarizer`)
4. Choose **Public** or **Private**
5. **DO NOT** initialize with README, .gitignore, or license (we already have these)
6. Click "Create repository"

## Step 3: Open Terminal in Your Project Folder

**Easiest way:**
1. Open File Explorer
2. Navigate to your project folder
3. Right-click in the folder → "Open in Terminal" or "Open PowerShell window here"

**Or manually:**
1. Open PowerShell
2. Run: `cd "C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer"`
   (Use quotes because the path has spaces!)

## Step 4: Initialize Git in Your Project

Now that you're in the project folder, run:

```bash
# Navigate to your project folder
cd "C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer"

# Initialize git repository
git init

# Add all files (except those in .gitignore)
git add .

# Create first commit
git commit -m "Initial commit: Meeting PDF Summarizer"

# Add your GitHub repository as remote (replace YOUR_USERNAME and REPO_NAME)
git remote add origin https://github.com/YOUR_USERNAME/REPO_NAME.git

# Rename main branch (if needed)
git branch -M main

# Push to GitHub
git push -u origin main
```

## Step 5: Share with Your Friend

1. Give your friend the repository URL: `https://github.com/YOUR_USERNAME/REPO_NAME`
2. They can clone it with:
   ```bash
   git clone https://github.com/YOUR_USERNAME/REPO_NAME.git
   cd REPO_NAME
   ```

## Alternative: Using GitHub Desktop

If you prefer a GUI:

1. Download GitHub Desktop: https://desktop.github.com/
2. Sign in with your GitHub account
3. File → Add Local Repository → Select your project folder
4. Click "Publish repository" button
5. Choose name and visibility, then publish

## What Gets Uploaded

✅ **Included:**
- `main.py` - Main code
- `requirements.txt` - Dependencies
- `roles.json` - Role definitions
- `README.md` - Documentation
- `.gitignore` - Ignore rules
- `transcripts/` - Sample transcripts

❌ **Excluded (via .gitignore):**
- `__pycache__/` - Python cache
- `out/*.pdf` - Generated PDFs
- `.env` - Environment variables (if you create one)
- Virtual environments

## Security Note

- Never commit `.env` files with API keys or secrets
- The `.gitignore` already excludes `.env` files
- Your friend will need to set up their own Ollama configuration

## Next Steps for Your Friend

After cloning, your friend should:

1. Install dependencies: `pip install -r requirements.txt`
2. Install and run Ollama: https://ollama.ai/
3. Pull the model: `ollama pull llama3.1:8b`
4. Run the script: `python main.py --input transcripts/tiny.txt --output out/test.pdf`
