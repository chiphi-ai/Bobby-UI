# CRITICAL: Box Scope Configuration - MUST DO THIS FIRST

## The Problem
You're getting "insufficient_scope" errors because Box scopes are configured in Box Developer Console, NOT in the OAuth URL. Even if you reconnect, the token won't have permissions unless scopes are configured in Box Developer Console FIRST.

## The Solution (Do This NOW)

### Step 1: Go to Box Developer Console
1. Visit: **https://developer.box.com/**
2. Log in with your Box account
3. Go to **My Apps** → Select your app (the one with Client ID: `dhpdciuin7211aokorgxcucx1xcayuig`)

### Step 2: Configure Application Scopes (CRITICAL)
1. Click on the **Configuration** tab
2. Scroll down to **Application Scopes** section
3. **CHECK THIS BOX** (this is the most important step):
   - ✅ **"Read and write all files and folders stored in Box"**
4. **DO NOT** check other scopes unless you need them
5. Click **Save Changes** at the bottom of the page
6. **WAIT 2-3 MINUTES** for changes to propagate

### Step 3: Verify Redirect URI (if not already done)
1. Still in **Configuration** tab
2. Scroll to **OAuth 2.0 Redirect URI** section
3. Make sure this URI is listed:
   - `http://localhost:5000/connect/box/callback`
4. If not, add it and click **Save Changes**

### Step 4: Reconnect Box in Your App
1. Go to your app: **Settings → Connected Apps**
2. Find **Box** and click **Disconnect**
3. Click **Connect** again
4. Authorize the app
5. The connection should now work!

## Why This Happens
- Box doesn't accept scope parameters in the OAuth URL
- Scopes MUST be configured in Box Developer Console
- Even if you reconnect 100 times, the token won't have permissions unless scopes are configured in Box Developer Console FIRST
- The app now verifies scopes immediately after connection - if scopes are missing, you'll see an error right away

## Verification
After connecting, you should see in the logs:
```
[SUCCESS] Box token scope verification passed - token has required permissions
```

If you see an error about insufficient scopes, go back to Step 2 and make sure you:
1. Checked the box for "Read and write all files and folders stored in Box"
2. Clicked "Save Changes"
3. Waited 2-3 minutes

## Still Not Working?
1. Double-check the scope is enabled in Box Developer Console
2. Make sure you clicked "Save Changes" (not just checked the box)
3. Wait 5 minutes and try again (Box changes can take time)
4. Try disconnecting and reconnecting again
5. Check the Flask console for detailed error messages
