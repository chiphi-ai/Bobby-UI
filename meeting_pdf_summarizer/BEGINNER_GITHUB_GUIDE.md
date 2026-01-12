# Complete Beginner's Guide to Putting Your Project on GitHub

This guide assumes you know nothing about Git or GitHub. Follow each step exactly!

## Part 1: Create a GitHub Account

1. **Go to:** https://github.com
2. **Click** "Sign up" (top right)
3. **Enter:**
   - Username (e.g., "james-smith" or "james123")
   - Email address
   - Password
4. **Click** "Create account"
5. **Verify your email** (check your inbox)
6. **Complete the setup** (you can skip the questions if you want)

✅ **You now have a GitHub account!**

---

## Part 2: Create a New Repository on GitHub

⚠️ **Skip this part if you already have a repository!** If you already created a repository on GitHub (like "Phi-AI"), skip to **Part 3** to install Git.

1. **Log into GitHub** (if not already logged in)

2. **Click the green "New" button** (or the "+" icon in top right → "New repository")

3. **Fill in the form:**
   - **Repository name:** `meeting-pdf-summarizer` (or whatever you want)
   - **Description:** "AI-powered meeting summary generator" (optional)
   - **Visibility:** Choose **Public** (anyone can see) or **Private** (only you)
   - **IMPORTANT:** Do NOT check any boxes:
     - ❌ Don't check "Add a README file"
     - ❌ Don't check "Add .gitignore"
     - ❌ Don't check "Choose a license"
   - (We already have these files!)

4. **Click the green "Create repository" button**

5. **You'll see a page with instructions - DON'T follow those yet!** Just copy the repository URL from the top. It looks like:
   ```
   https://github.com/YOUR_USERNAME/meeting-pdf-summarizer.git
   ```
   Save this URL somewhere - you'll need it!

✅ **Your empty repository is created on GitHub!**

---

## Part 3: Install Git on Your Computer

⚠️ **MUST DO THIS FIRST!** You cannot use Git commands until Git is installed. If you skip this step, you'll get "git: command not found" errors.

1. **Go to:** https://git-scm.com/download/win

2. **Click** "Download for Windows" (it will auto-detect your system)

3. **Run the installer:**
   - Click "Next" through all the screens
   - Use the **default settings** (don't change anything)
   - Click "Install"
   - Wait for it to finish
   - Click "Finish"

4. **Restart your computer** (or at least close and reopen PowerShell/Terminal)

✅ **Git is now installed!**

---

## Part 4: Open Terminal in Your Project Folder

### Method A: The Easy Way (Recommended)

1. **Open File Explorer** (the folder icon on your taskbar)

2. **Navigate to your project:**
   - Go to: `C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer`
   - Or just search for "meeting_pdf_summarizer" in File Explorer

3. **Right-click on an empty space** in the folder (not on a file)

4. **Click** "Open in Terminal" or "Open PowerShell window here"

5. **A black window opens** - this is your terminal! You should see something like:
   ```
   PS C:\Users\james\...\meeting_pdf_summarizer>
   ```

✅ **You're now in your project folder!**

### Method B: Manual Way (If Method A doesn't work)

1. **Press** `Windows Key + X`
2. **Click** "Windows PowerShell" or "Terminal"
3. **Type this command** (copy and paste it):
   ```powershell
   cd "C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer"
   ```
4. **Press Enter**
5. **Type:** `dir` and press Enter
6. **You should see** `main.py`, `requirements.txt`, etc.

✅ **You're in the right place!**

---

## Part 5: Initialize Git and Upload Your Files

⚠️ **IMPORTANT:** Before starting, make sure you completed **Part 3** and installed Git! If you get "git: command not found", go back to Part 3.

Now we'll run commands one by one. **Copy each command, paste it in the terminal, press Enter, wait for it to finish, then do the next one.**

⚠️ **CRITICAL:** When you see code blocks like this:
```
```powershell
git --version
```
```
**ONLY copy the command inside** (like `git --version`). **DO NOT copy** the ````powershell` or ``` parts - those are just formatting markers!

### Step 1: Check Git is Working
```powershell
git --version
```
**Expected output:** Something like `git version 2.xx.x`  
✅ If you see a version number, Git is working!

❌ **If you see "git: command not found":** Go back to **Part 3** and install Git first, then restart your terminal!

### Step 2: Initialize Git
```powershell
git init
```
**Expected output:** `Initialized empty Git repository in ...`  
✅ Git is now tracking your folder!

### Step 3: Check What Files Will Be Added
```powershell
git status
```
**Expected output:** You'll see a list of files in red (untracked files)  
✅ This shows what will be uploaded!

### Step 4: Add All Files
```powershell
git add .
```
**Expected output:** Nothing (this is normal - no output means success!)  
✅ Files are staged to be uploaded!

### Step 5: Create Your First Commit
```powershell
git commit -m "Initial commit: Meeting PDF Summarizer"
```
**Expected output:** Something like `[main (root-commit) abc123] Initial commit...`  
✅ Your files are saved locally!

**Note:** If you get an error about email/name, run these first:
```powershell
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```
Then try the commit again.

### Step 6: Connect to Your Existing GitHub Repository

You already have a repository called "Phi-AI" on GitHub. Now we'll connect your local code to it.

**Option A: If you know your repository URL**
- Your repository URL should be: `https://github.com/YOUR_USERNAME/Phi-AI.git`
- Replace `YOUR_USERNAME` with your actual GitHub username
- Then run this command (copy only the command, not the ```powershell part):
```powershell
git remote add origin https://github.com/YOUR_USERNAME/Phi-AI.git
```

**Option B: If you don't know your repository URL, find it:**
1. Go to https://github.com and log in
2. Click on your "Phi-AI" repository
3. Click the green "Code" button (top right of the file list)
4. Copy the HTTPS URL (it looks like: `https://github.com/YOUR_USERNAME/Phi-AI.git`)
5. Use that URL in the command above

**Example** (if your username is "jplukish"):
```powershell
git remote add origin https://github.com/jplukish/Phi-AI.git
```

**Expected output:** Nothing (success!)  
✅ Your local folder is now connected to your GitHub repository!

**Note:** If you get an error saying "remote origin already exists", that's okay - it means you already connected it. You can skip to Step 7.

### Step 7: Rename Branch to "main" (if needed)
```powershell
git branch -M main
```
**Expected output:** Nothing (success!)  
✅ Your branch is named correctly!

### Step 8: Upload to GitHub
```powershell
git push -u origin main
```
**What happens:**
- You might be asked to log in
- If asked for username: Enter your GitHub username
- If asked for password: Enter a **Personal Access Token** (see Part 6 if this happens)
- Wait... this might take a minute
- You'll see progress bars and "Writing objects..."

**Expected output:** Something like `Branch 'main' set up to track remote branch 'main'`  
✅ **YOUR FILES ARE NOW ON GITHUB!**

**If your repository already has files:**
- If you get an error about "unrelated histories" or "failed to push", your repository might already have files
- **Option 1 (Recommended):** Pull the existing files first, then push:
  ```powershell
  git pull origin main --allow-unrelated-histories
  ```
  (If it asks for a commit message, just press Enter)
  Then try `git push -u origin main` again
  
- **Option 2 (Only if you want to replace everything):** Force push (⚠️ This will overwrite files on GitHub):
  ```powershell
  git push -u origin main --force
  ```

---

## Part 6: If You're Asked for a Password

GitHub doesn't use your regular password anymore. You need a **Personal Access Token**:

1. **Go to GitHub.com** → Click your profile picture (top right) → **Settings**

2. **Scroll down** → Click **"Developer settings"** (left sidebar)

3. **Click** "Personal access tokens" → **"Tokens (classic)"**

4. **Click** "Generate new token" → **"Generate new token (classic)"**

5. **Fill in:**
   - **Note:** "Meeting Summarizer Project"
   - **Expiration:** Choose how long (90 days is good)
   - **Scopes:** Check **"repo"** (this gives full repository access)

6. **Click** "Generate token" (bottom)

7. **COPY THE TOKEN IMMEDIATELY** (you won't see it again!)
   - It looks like: `ghp_xxxxxxxxxxxxxxxxxxxx`

8. **Go back to your terminal** and try `git push` again
   - When asked for password: **Paste the token** (not your GitHub password!)

---

## Part 7: Verify It Worked

1. **Go to your GitHub repository page:**
   ```
   https://github.com/YOUR_USERNAME/REPO_NAME
   ```

2. **Refresh the page** (F5)

3. **You should see:**
   - ✅ All your files (`main.py`, `requirements.txt`, etc.)
   - ✅ Your README.md
   - ✅ The `transcripts/` folder

✅ **SUCCESS! Your project is on GitHub!**

---

## Part 8: Share with Your Friend

1. **Copy your repository URL:**
   ```
   https://github.com/YOUR_USERNAME/REPO_NAME
   ```

2. **Send it to your friend**

3. **They can:**
   - View it online
   - Clone it (download it) using: `git clone https://github.com/YOUR_USERNAME/REPO_NAME.git`

---

## Troubleshooting

### "git: command not found"
- Git isn't installed or not in PATH
- Reinstall Git and **restart your computer**
- Or restart PowerShell/Terminal

### "fatal: not a git repository"
- You're not in the right folder
- Run: `cd "C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer"`
- Then try again

### "Permission denied" or "Authentication failed"
- You need a Personal Access Token (see Part 6)
- Make sure you copied the token correctly

### "Everything up-to-date" but files aren't on GitHub
- You might not have added files
- Run: `git add .` then `git commit -m "Add files"` then `git push`

### Files are missing on GitHub
- Check your `.gitignore` file - some files are intentionally excluded
- PDFs in `out/` folder won't be uploaded (this is normal!)

---

## Quick Command Reference

```powershell
# Navigate to project
cd "C:\Users\james\OneDrive - Massachusetts Institute of Technology\meeting_pdf_summarizer"

# Initialize
git init
git add .
git commit -m "Initial commit"

# Connect to GitHub
git remote add origin https://github.com/YOUR_USERNAME/REPO_NAME.git
git branch -M main
git push -u origin main
```

---

## You're Done! 🎉

Your project is now on GitHub and your friend can download it!

**Next time you make changes:**
```powershell
git add .
git commit -m "Description of what you changed"
git push
```

That's it! You're now a Git user! 🚀
