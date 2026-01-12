# How to Run Commands in Your Project Folder

## Method 1: Using File Explorer (Easiest)

1. **Open File Explorer** and navigate to your project folder:
   ```
   C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer
   ```

2. **Right-click in the folder** (on an empty area, not a file)

3. **Select one of these options:**
   - **"Open in Terminal"** (Windows 11)
   - **"Open PowerShell window here"** (Windows 10)
   - **"Git Bash Here"** (if you installed Git Bash)

4. The terminal will open **already in your project folder** - you're ready to go!

## Method 2: Using PowerShell/Command Prompt

1. **Open PowerShell or Command Prompt:**
   - Press `Windows Key + X`
   - Select "Windows PowerShell" or "Terminal"

2. **Navigate to your project folder:**
   ```powershell
   cd "C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer"
   ```
   
   **Note:** Use quotes because the path has spaces!

3. **Verify you're in the right place:**
   ```powershell
   pwd
   # or
   dir
   ```
   
   You should see `main.py`, `requirements.txt`, etc.

## Method 3: Using VS Code (If You Use It)

1. **Open VS Code**

2. **File → Open Folder** → Select your project folder

3. **Open Terminal in VS Code:**
   - Press `` Ctrl + ` `` (backtick)
   - Or: Terminal → New Terminal

4. The terminal opens **already in your project folder**

## Quick Commands to Run

Once you're in the project folder, you can run:

### Check if Git is installed:
```bash
git --version
```

### Initialize Git repository:
```bash
git init
```

### See what files will be added:
```bash
git status
```

### Add all files:
```bash
git add .
```

### Create first commit:
```bash
git commit -m "Initial commit: Meeting PDF Summarizer"
```

### Connect to GitHub (replace with your URL):
```bash
git remote add origin https://github.com/YOUR_USERNAME/REPO_NAME.git
```

### Push to GitHub:
```bash
git push -u origin main
```

## Troubleshooting

### "The system cannot find the path specified"
- Make sure you used quotes around the path: `cd "C:\Users\..."`
- Check that the folder path is correct
- Try using forward slashes: `cd C:/Users/james/OneDrive...`

### "git: command not found"
- Git isn't installed or not in PATH
- Install Git: https://git-scm.com/download/win
- Restart your terminal after installing

### "Permission denied"
- Make sure you have write permissions to the folder
- Try running PowerShell as Administrator (right-click → Run as Administrator)

## Pro Tip: Create a Shortcut

You can create a desktop shortcut that opens PowerShell in your project folder:

1. Right-click desktop → New → Shortcut
2. Location: `powershell.exe -NoExit -Command "cd 'C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer'"`
3. Name it "Meeting Summarizer Project"
4. Double-click to open PowerShell in your project folder instantly!
