# Git Push Instructions

This guide will help you push the code to Git for your team to use.

## Prerequisites

1. **Install Git** (if not already installed)
   - Download from: https://git-scm.com/downloads
   - Verify installation: Open terminal and run `git --version`

2. **Create a Git Repository**
   - Option A: Create on GitHub/GitLab/Bitbucket
   - Option B: Use existing repository URL

## Step-by-Step Instructions

### Step 1: Initialize Git Repository (if not already initialized)

Open terminal/PowerShell in the project directory and run:

```bash
cd C:\Users\bjones25\Documents\Dio
git init
```

### Step 2: Add All Files

```bash
git add .
```

This adds all files to staging (except those in `.gitignore`).

### Step 3: Create Initial Commit

```bash
git commit -m "Initial commit: Phi AI Meeting Transcription System"
```

### Step 4: Add Remote Repository

**If creating a new repository on GitHub:**

1. Go to https://github.com and create a new repository
2. **Don't** initialize with README, .gitignore, or license
3. Copy the repository URL (e.g., `https://github.com/username/repo-name.git`)

**Then run:**
```bash
git remote add origin <repository-url>
```

Replace `<repository-url>` with your actual repository URL.

**If using an existing repository:**
```bash
git remote add origin <existing-repository-url>
```

### Step 5: Push to Remote

```bash
git branch -M main
git push -u origin main
```

If you're prompted for credentials:
- **GitHub**: Use a Personal Access Token (not your password)
  - Create one at: https://github.com/settings/tokens
  - Select scope: `repo`

## Alternative: Using GitHub Desktop (Easier)

If you prefer a GUI:

1. **Download GitHub Desktop**: https://desktop.github.com/
2. **Install and sign in** with your GitHub account
3. **Add the repository**:
   - File → Add Local Repository
   - Select: `C:\Users\bjones25\Documents\Dio`
4. **Commit changes**:
   - Write commit message: "Initial commit: Phi AI Meeting Transcription System"
   - Click "Commit to main"
5. **Publish repository**:
   - Click "Publish repository"
   - Choose name and visibility
   - Click "Publish repository"

## Verifying the Push

After pushing, verify by:

1. **Check remote repository** - Visit your GitHub/GitLab repository in browser
2. **Verify files are there** - You should see:
   - `web_app.py`
   - `requirements.txt`
   - `README.md`
   - `SETUP_INSTRUCTIONS.md`
   - All other project files (except those in `.gitignore`)

## Important: What Gets Pushed

✅ **Will be pushed:**
- All Python files (`.py`)
- Templates (`.html`)
- Static files (CSS, images)
- Configuration files (`config.json`, `organizations_directory.json`)
- Documentation (`.md` files)
- `requirements.txt`

❌ **Will NOT be pushed** (protected by `.gitignore`):
- `.env` file (contains API keys)
- `users.csv` (user data)
- `enroll/*.wav`, `enroll/*.webm` (enrollment audio files)
- `output/` files (generated transcripts)
- `__pycache__/` (Python cache)
- `venv/` (virtual environment)

## Sharing with Your Team

Once pushed, share with your team:

1. **Repository URL** - Give them the Git repository URL
2. **Access** - Add them as collaborators (GitHub) or give them access
3. **Instructions** - Share `SETUP_INSTRUCTIONS.md` with them

## Future Updates

When you make changes and want to push updates:

```bash
# 1. Check what changed
git status

# 2. Add changed files
git add .

# 3. Commit changes
git commit -m "Description of changes"

# 4. Push to remote
git push
```

## Troubleshooting

### "git: command not found"
- Git is not installed or not in PATH
- Install Git from https://git-scm.com/downloads
- Restart terminal after installation

### "fatal: not a git repository"
- You're not in the project directory
- Run: `cd C:\Users\bjones25\Documents\Dio`
- Or initialize: `git init`

### "Permission denied" or "Authentication failed"
- **GitHub**: Use Personal Access Token instead of password
  - Create at: https://github.com/settings/tokens
  - Use token as password when prompted
- **GitLab/Bitbucket**: Check your credentials

### "remote origin already exists"
- The remote is already configured
- Check with: `git remote -v`
- To change: `git remote set-url origin <new-url>`

### "failed to push some refs"
- Someone else pushed changes
- Pull first: `git pull origin main`
- Then push again: `git push`

## Quick Reference Commands

```bash
# Check status
git status

# Add all files
git add .

# Commit changes
git commit -m "Your message here"

# Push to remote
git push

# Pull latest changes
git pull

# View commit history
git log

# Check remote repository
git remote -v
```

## Next Steps

After pushing:

1. ✅ Verify files are on remote repository
2. ✅ Share repository URL with team
3. ✅ Share `SETUP_INSTRUCTIONS.md` with team
4. ✅ Add team members as collaborators (if using GitHub)

Your team can now clone and set up the project using `SETUP_INSTRUCTIONS.md`!
