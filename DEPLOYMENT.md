# Deployment Guide for PythonAnywhere

This guide walks you through deploying Phi Ai to PythonAnywhere with your domain `phiartificialintelligence.com`.

## Prerequisites
- ✅ PythonAnywhere account (paid subscription)
- ✅ Domain name: `phiartificialintelligence.com`
- ✅ All code files ready

## Step-by-Step Deployment

### 1. Upload Files to PythonAnywhere

1. Go to PythonAnywhere → **Files** tab
2. Create a new directory: `phiai` (or any name you prefer)
3. Upload ALL files:
   - All `.py` files (web_app.py, transcribe_assemblyai.py, identify_speakers.py, email_named_script.py, etc.)
   - `requirements.txt`
   - `wsgi.py` (this file)
   - `templates/` folder (upload entire folder)
   - `static/` folder (if it exists with logo)
   
   **DO NOT upload:**
   - `.env` file (we'll use environment variables instead)
   - `input/`, `output/`, `enroll/` folders (they'll be created automatically)

### 2. Open Bash Console

1. Go to **Consoles** tab → Click **"Bash"**
2. Navigate to your project:
   ```bash
   cd ~/phiai
   ```

### 3. Create Required Directories

```bash
mkdir -p input output enroll static/images
```

### 4. Install Python Dependencies

```bash
pip3.10 install --user -r requirements.txt
```

**Note:** This may take 5-10 minutes (PyTorch and SpeechBrain are large).

### 5. Install FFmpeg (CRITICAL)

FFmpeg is required for audio processing. PythonAnywhere doesn't have it by default.

**Option A (Recommended):** Contact PythonAnywhere Support
- Go to **Help & Documentation** → **Contact Support**
- Request: "Can you install ffmpeg system-wide? I need it for audio processing in my Flask app."
- They usually install it within a few hours

**Option B:** If they won't install it, use conda:
```bash
conda create -n phiai python=3.10
conda activate phiai
conda install -c conda-forge ffmpeg
pip install -r requirements.txt
```
(Then configure your web app to use the conda environment)

**Verify ffmpeg is installed:**
```bash
ffmpeg -version
```
If it says "command not found", you MUST get support to install it.

### 6. Configure Web App

1. Go to **Web** tab
2. Click **"Add a new web app"**
3. Choose **"Manual configuration"** → Next
4. Select **Python 3.10** → Next
5. It will create a placeholder WSGI file

### 7. Edit WSGI File

1. In **Web** tab, find the link to edit your WSGI file
   - Usually: `/var/www/yourusername_pythonanywhere_com_wsgi.py`
   - Or similar path shown in the Web tab

2. **Delete all contents** and paste the contents of `wsgi.py`, but:
   - **IMPORTANT:** Change `/home/yourusername/phiai` to match YOUR path
   - Replace `yourusername` with your actual PythonAnywhere username
   - Example: If your username is `bobbynedjones`, change to `/home/bobbynedjones/phiai`

3. Save the file

### 8. Set Environment Variables

In **Web** tab, scroll to **"Environment variables"** section:

Click **"Add a new variable"** for each:

```
ASSEMBLYAI_API_KEY = 98e0a0993e5b4849810df8cfced7aca4
SMTP_HOST = smtp.gmail.com
SMTP_PORT = 587
SMTP_USER = mail@phiartificialintelligence.com
SMTP_PASS = ejpntssypayiioel
BASE_URL = https://phiartificialintelligence.com
```

**Note:** These override the defaults in the code. Always set BASE_URL to your actual domain.

### 9. Configure Source Code & Working Directory

In **Web** tab, under **"Code"** section:
- **Source code:** `/home/yourusername/phiai` (adjust `yourusername`)
- **Working directory:** `/home/yourusername/phiai` (same path)

### 10. Reload Web App

1. Click the big green **"Reload"** button in the Web tab
2. Wait 10-20 seconds for it to reload

### 11. Test with Temporary URL

1. Visit: `https://yourusername.pythonanywhere.com`
   - Replace `yourusername` with your actual username

2. Test the site:
   - Try signup
   - Try login
   - Check if pages load correctly

3. **Check for errors:**
   - Go to **Files** tab
   - Navigate to: `/var/log/yourusername.pythonanywhere.com.error.log`
   - Click to view recent errors

**Common errors:**
- **Import errors:** Make sure all files are uploaded
- **Module not found:** Run `pip3.10 install --user package_name` again
- **FFmpeg not found:** Contact support (see step 5)

### 12. Connect Your Domain

1. In **Web** tab, go to **"Domains"** section
2. Enter: `phiartificialintelligence.com`
3. Click **"Add"**
4. PythonAnywhere will show you an **IP address** (like `185.x.x.x`)

5. **Configure DNS at your domain registrar:**
   - Log into where you bought `phiartificialintelligence.com` (GoDaddy, Namecheap, etc.)
   - Go to DNS settings
   - Add an **A record**:
     - **Host/Name:** `@` (or leave blank, depending on registrar)
     - **Type:** `A`
     - **Value/Points to:** The IP address PythonAnywhere gave you
     - **TTL:** 3600 (or default)

6. **Optional - Add www subdomain:**
   - Add another A record:
     - **Host/Name:** `www`
     - **Type:** `A`
     - **Value:** Same IP address
     - Or set up redirect to main domain in PythonAnywhere

7. **Wait for DNS propagation:**
   - Usually takes 1-24 hours
   - Check with: `ping phiartificialintelligence.com` (should show PythonAnywhere IP)

8. **Enable SSL/HTTPS:**
   - In **Web** tab, find **"SSL"** section
   - Click **"Add a new SSL certificate"**
   - Choose **"PythonAnywhere domain"** (they auto-generate Let's Encrypt cert)
   - Wait a few minutes for it to generate
   - Your site will now use HTTPS!

### 13. Final Verification

✅ Visit `https://phiartificialintelligence.com`
✅ Test all features:
   - Signup new account
   - Login
   - Record/enroll voice
   - Account settings
   - Password reset

## Troubleshooting

### Check Error Logs
```bash
tail -n 50 /var/log/yourusername.pythonanywhere.com.error.log
```

### Test FFmpeg
```bash
ffmpeg -version
```
If missing, contact PythonAnywhere support.

### Check File Permissions
```bash
cd ~/phiai
ls -la
chmod 755 ~/phiai
```

### Reinstall Dependencies
```bash
pip3.10 install --user --upgrade -r requirements.txt
```

### Force Reload Web App
- Web tab → Click "Reload" button again
- Sometimes takes 30-60 seconds

## Maintenance

### Update Code
1. Upload new files via Files tab
2. Click "Reload" in Web tab

### View Logs
- Error log: `/var/log/yourusername.pythonanywhere_com.error.log`
- Access log: `/var/log/yourusername.pythonanywhere_com.access.log`

### Environment Variables
- Always update in Web tab → Environment variables
- These take precedence over `.env` file

## Notes

- **File Watcher:** The folder watching feature is disabled by default in production (it was for Box sync). The web app will handle uploads directly.
- **Static Files:** Make sure your logo is in `static/images/phi-ai-logo.png`
- **Database:** User data is stored in `input/users.csv` - this file will persist across reloads
- **Output Files:** Transcripts and processed files go to `output/` directory

## Support

If you encounter issues:
1. Check error logs first
2. Verify environment variables are set correctly
3. Make sure ffmpeg is installed
4. Contact PythonAnywhere support for server-level issues

Good luck with your deployment! 🚀
