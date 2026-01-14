# OAuth Setup Guide for Google Drive and Box

## Box OAuth Setup

### Problem: `redirect_uri_mismatch` Error

The error occurs because the redirect URI in your Box app settings doesn't match what the application is sending.

### Solution:

1. **Go to Box Developer Console**
   - Visit: https://developer.box.com/
   - Log in with your Box account
   - Go to **My Apps** → Select your app (or create a new one)

2. **Configure Application Scopes (CRITICAL)**
   - Go to **Configuration** tab
   - Scroll down to **Application Scopes** section
   - **Enable the following scopes** (check the boxes):
     - ✅ **Read and write all files and folders stored in Box**
     - ✅ **Manage users** (if available, optional but recommended)
   - **IMPORTANT**: Without these scopes, you'll get "insufficient_scope" errors when trying to upload files
   - Click **Save Changes** (important: you must save!)

3. **Add Redirect URI**
   - Still in **Configuration** tab
   - Scroll down to **OAuth 2.0 Redirect URI** section
   - Click **Add Redirect URI**
   - Add this **exact** URI:
     - `http://localhost:5000/connect/box/callback`
   - **Critical Requirements**: 
     - Use `http://` (NOT `https://`)
     - Use `localhost` (NOT `127.0.0.1`)
     - Include the port number `:5000`
     - Include the full path `/connect/box/callback`
     - **NO trailing slash** at the end
     - **Case-sensitive** - must match exactly
   - Click **Save Changes** (important: you must save!)

4. **Verify App Settings**
   - Make sure your app is **Active** (not disabled)
   - Check that **OAuth 2.0** is enabled for your app
   - Verify the **Client ID** matches your `.env` file

4. **Verify Environment Variables**
   - Make sure your `.env` file has:
     ```
     BOX_CLIENT_ID=dhpdciuin7211aokorgxcucx1xcayuig
     BOX_CLIENT_SECRET=your_client_secret_here
     ```
   - The Client ID should match what's shown in Box Developer Console

5. **Check Console Output**
   - When you try to connect, check your Flask console/terminal
   - You should see debug output like:
     ```
     [Box OAuth] Redirect URI: http://localhost:5000/connect/box/callback
     [Box OAuth] Client ID: dhpdciuin7211aokorgxcucx1xcayuig
     ```
   - Verify the redirect URI matches exactly what you registered

6. **Common Issues & Fixes**

   **Issue**: Still getting `redirect_uri_mismatch` after adding URI
   - **Fix 1**: Wait 2-3 minutes after saving - Box changes can take time to propagate
   - **Fix 2**: Make sure you clicked "Save Changes" (not just "Add")
   - **Fix 3**: Check for typos - compare character by character
   - **Fix 4**: Try removing and re-adding the redirect URI
   - **Fix 5**: Clear browser cache and cookies
   - **Fix 6**: Make sure your app is not in "Development" mode restrictions

   **Issue**: Box says "Application Error" or "Invalid Client"
   - **Fix**: Verify your Client ID and Secret are correct in `.env`
   - **Fix**: Make sure your Box app is active and OAuth 2.0 is enabled

7. **Test Again**
   - After making changes, wait 2-3 minutes
   - Try connecting to Box again
   - Check the Flask console for the exact redirect URI being used
   - The redirect URI should now match

---

## Google Drive OAuth Setup

### Problem: `Error 403: access_denied` - App Not Verified

The error occurs because your Google OAuth app is in "Testing" mode and your email isn't added as a test user.

### Solution Option 1: Add Test Users (Recommended for Development)

1. **Go to Google Cloud Console**
   - Visit: https://console.cloud.google.com/
   - Select your project
   - Go to **APIs & Services** → **OAuth consent screen**

2. **Add Test Users**
   - Scroll down to **Test users** section
   - Click **+ ADD USERS**
   - Add your email address: `bobbynedjones19@gmail.com`
   - Add any other emails that need access
   - Click **SAVE**

3. **Verify Redirect URI**
   - Go to **APIs & Services** → **Credentials**
   - Click on your OAuth 2.0 Client ID
   - Under **Authorized redirect URIs**, make sure you have:
     - `http://localhost:5000/connect/googledrive/callback`
     - `http://127.0.0.1:5000/connect/googledrive/callback` (optional)
   - Click **SAVE**

4. **Test Again**
   - Try connecting to Google Drive again
   - You should now be able to authorize the app

### Solution Option 2: Publish the App (For Production)

**Note**: Publishing requires Google verification if you use sensitive scopes. The current scope `drive.file` is considered sensitive.

1. **Go to OAuth Consent Screen**
   - Visit: https://console.cloud.google.com/apis/credentials/consent
   - Click **PUBLISH APP**
   - Follow Google's verification process
   - This can take several days

**For now, use Option 1 (Add Test Users) for development.**

---

## Quick Checklist

### Box:
- [ ] Redirect URI `http://localhost:5000/connect/box/callback` added in Box Developer Console
- [ ] `BOX_CLIENT_ID` and `BOX_CLIENT_SECRET` set in `.env` file
- [ ] App is saved and active in Box Developer Console

### Google Drive:
- [ ] Your email added as a test user in Google Cloud Console
- [ ] Redirect URI `http://localhost:5000/connect/googledrive/callback` added in Google Cloud Console
- [ ] `GOOGLE_DRIVE_CLIENT_ID` and `GOOGLE_DRIVE_CLIENT_SECRET` set in `.env` file
- [ ] OAuth consent screen configured (app name, support email, etc.)

---

## Common Issues

### Issue: "redirect_uri_mismatch" persists after adding URI

**Solutions:**
1. Make sure you saved the changes in Box/Google Console
2. Wait a few minutes for changes to propagate
3. Clear your browser cache and cookies
4. Make sure the URI matches exactly (including `http://` vs `https://`, port number, trailing slashes)

### Issue: Google still shows "access_denied" after adding test user

**Solutions:**
1. Make sure you added the exact email address you're using to log in
2. Try logging out and back into Google
3. Make sure the OAuth consent screen is in "Testing" mode (not "Published")
4. Check that your app's scopes include `https://www.googleapis.com/auth/drive.file`

---

## Need Help?

If you continue to have issues:
1. Check the exact error message in the browser console
2. Verify all environment variables are set correctly
3. Make sure your Flask app is running on port 5000
4. Check that the redirect URIs match exactly (case-sensitive, no trailing slashes)
