"""
Box API client service with automatic token refresh and scope verification.
"""
import os
import time
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime, timedelta

try:
    from boxsdk import Client, OAuth2
    from boxsdk.exception import BoxAPIException
    BOXSDK_AVAILABLE = True
except ImportError:
    BOXSDK_AVAILABLE = False
    BoxAPIException = Exception

# Import functions from web_app - use late import to avoid circular dependencies
def _get_web_app_funcs():
    """Get functions from web_app module (late import to avoid circular deps)."""
    import web_app
    return web_app.read_users, web_app.write_users, web_app.decrypt_token, web_app.encrypt_token


class BoxInsufficientScopeError(Exception):
    """Raised when Box token lacks required scopes for an operation."""
    pass


class BoxTokenError(Exception):
    """Raised when Box token is invalid or cannot be refreshed."""
    pass


def get_box_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Get Box OAuth credentials from environment."""
    client_id = os.getenv("BOX_CLIENT_ID")
    client_secret = os.getenv("BOX_CLIENT_SECRET")
    return client_id, client_secret


def refresh_box_token(user_email: str, refresh_token: str) -> Optional[Tuple[str, str, int]]:
    read_users, write_users, decrypt_token, encrypt_token = _get_web_app_funcs()
    """
    Refresh Box access token using refresh token.
    
    Returns:
        Tuple of (new_access_token, new_refresh_token, expires_at_timestamp) or None if refresh failed
    """
    try:
        BOX_CLIENT_ID, BOX_CLIENT_SECRET = get_box_credentials()
        
        if not BOX_CLIENT_ID or not BOX_CLIENT_SECRET:
            print(f"[Box] Credentials not configured, cannot refresh token for {user_email}")
            return None
        
        if not refresh_token:
            print(f"[Box] No refresh token available for {user_email}")
            return None
        
        print(f"[Box] Refreshing token for {user_email}...")
        
        import requests
        token_response = requests.post(
            "https://api.box.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": BOX_CLIENT_ID,
                "client_secret": BOX_CLIENT_SECRET
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10
        )
        
        if token_response.status_code != 200:
            error_data = token_response.json() if token_response.text else {}
            error_msg = error_data.get("error_description", error_data.get("error", "Unknown error"))
            print(f"[Box] Token refresh failed: {error_msg}")
            return None
        
        token_data = token_response.json()
        new_access_token = token_data.get("access_token")
        new_refresh_token = token_data.get("refresh_token", refresh_token)  # May rotate or stay same
        expires_in = token_data.get("expires_in", 3600)  # Default 1 hour (3600 seconds)
        
        if not new_access_token:
            print(f"[Box] No access token in refresh response")
            return None
        
        # Calculate new expiration (with 2 min buffer)
        new_expires_at = int(time.time()) + expires_in - 120
        
        # Update stored tokens
        users = read_users()
        if user_email.lower() in users:
            if "connected_apps" not in users[user_email.lower()]:
                users[user_email.lower()]["connected_apps"] = {}
            if "box" not in users[user_email.lower()]["connected_apps"]:
                users[user_email.lower()]["connected_apps"]["box"] = {}
            
            users[user_email.lower()]["connected_apps"]["box"]["access_token_encrypted"] = encrypt_token(new_access_token)
            if new_refresh_token:
                users[user_email.lower()]["connected_apps"]["box"]["refresh_token_encrypted"] = encrypt_token(new_refresh_token)
            users[user_email.lower()]["connected_apps"]["box"]["token_expires_at"] = new_expires_at
            
            write_users(users)
            print(f"[Box] Token refreshed successfully, expires at {new_expires_at}")
        
        return (new_access_token, new_refresh_token, new_expires_at)
        
    except Exception as e:
        print(f"[Box] Exception refreshing token: {e}")
        import traceback
        if os.getenv("FLASK_DEBUG") == "1":
            traceback.print_exc()
        return None


def refresh_if_needed(user_email: str) -> bool:
    """
    Refresh Box token if it's expired or expiring soon.
    
    Returns:
        True if token was refreshed or is still valid, False if refresh failed
    """
    if not BOXSDK_AVAILABLE:
        return False
    
    read_users, write_users, decrypt_token, encrypt_token = _get_web_app_funcs()
    users = read_users()
    user_data = users.get(user_email.lower())
    if not user_data or "connected_apps" not in user_data or "box" not in user_data["connected_apps"]:
        return False
    
    box_config = user_data["connected_apps"]["box"]
    access_token_enc = box_config.get("access_token_encrypted")
    refresh_token_enc = box_config.get("refresh_token_encrypted")
    token_expires_at = box_config.get("token_expires_at")
    
    if not access_token_enc:
        return False
    
    # Check if token needs refresh (within 2 minutes of expiration)
    current_time = int(time.time())
    if token_expires_at and (token_expires_at - current_time) < 120:
        print(f"[Box] Token expires soon (in {token_expires_at - current_time}s), refreshing...")
        if refresh_token_enc:
            refresh_token = decrypt_token(refresh_token_enc)
            refresh_result = refresh_box_token(user_email, refresh_token)
            if refresh_result:
                print(f"[Box] Token refreshed successfully")
                return True
            else:
                print(f"[Box] Token refresh failed")
                return False
        else:
            print(f"[Box] No refresh token available, cannot refresh")
            return False
    
    return True


def get_authenticated_client(user_email: str) -> Optional[Client]:
    read_users, write_users, decrypt_token, encrypt_token = _get_web_app_funcs()
    """
    Get authenticated Box client with automatic token refresh.
    
    Returns:
        Box Client instance or None if authentication fails
    """
    if not BOXSDK_AVAILABLE:
        print("[Box] boxsdk package not installed")
        return None
    
    BOX_CLIENT_ID, BOX_CLIENT_SECRET = get_box_credentials()
    if not BOX_CLIENT_ID or not BOX_CLIENT_SECRET:
        print("[Box] Credentials not configured")
        return None
    
    users = read_users()
    user_data = users.get(user_email.lower())
    if not user_data or "connected_apps" not in user_data or "box" not in user_data["connected_apps"]:
        print(f"[Box] No Box connection found for {user_email}")
        return None
    
    box_config = user_data["connected_apps"]["box"]
    access_token_enc = box_config.get("access_token_encrypted")
    refresh_token_enc = box_config.get("refresh_token_encrypted")
    
    if not access_token_enc:
        print(f"[Box] No access token for {user_email}")
        return None
    
    access_token = decrypt_token(access_token_enc)
    refresh_token = decrypt_token(refresh_token_enc) if refresh_token_enc else None
    
    # Refresh if needed
    if not refresh_if_needed(user_email):
        if refresh_token:
            print(f"[Box] Token refresh failed, but continuing with current token")
        else:
            print(f"[Box] No refresh token available")
    
    # Re-read after potential refresh
    read_users, write_users, decrypt_token, encrypt_token = _get_web_app_funcs()
    users = read_users()
    box_config = users[user_email.lower()]["connected_apps"]["box"]
    access_token = decrypt_token(box_config["access_token_encrypted"])
    refresh_token = decrypt_token(box_config["refresh_token_encrypted"]) if box_config.get("refresh_token_encrypted") else None
    
    # Create OAuth2 object
    oauth = OAuth2(
        client_id=BOX_CLIENT_ID,
        client_secret=BOX_CLIENT_SECRET,
        access_token=access_token,
        refresh_token=refresh_token
    )
    
    # Set up refresh callback to update stored tokens
    def store_tokens(access_token_new, refresh_token_new):
        """Callback to store refreshed tokens"""
        read_users_local, write_users_local, decrypt_token_local, encrypt_token_local = _get_web_app_funcs()
        try:
            users = read_users_local()
            if user_email.lower() in users:
                if "connected_apps" not in users[user_email.lower()]:
                    users[user_email.lower()]["connected_apps"] = {}
                if "box" not in users[user_email.lower()]["connected_apps"]:
                    users[user_email.lower()]["connected_apps"]["box"] = {}
                
                users[user_email.lower()]["connected_apps"]["box"]["access_token_encrypted"] = encrypt_token_local(access_token_new)
                if refresh_token_new:
                    users[user_email.lower()]["connected_apps"]["box"]["refresh_token_encrypted"] = encrypt_token_local(refresh_token_new)
                expires_at = int(time.time()) + 3600 - 120  # Default 1 hour
                users[user_email.lower()]["connected_apps"]["box"]["token_expires_at"] = expires_at
                write_users_local(users)
                print(f"[Box] Tokens updated after SDK auto-refresh")
        except Exception as e:
            print(f"[Box] Failed to store refreshed tokens: {e}")
    
    oauth.refresh = store_tokens
    
    try:
        client = Client(oauth)
        # Verify connection by getting user info
        client.user(user_id='me').get()
        print(f"[Box] Authenticated client created for {user_email}")
        return client
    except Exception as e:
        print(f"[Box] Failed to create authenticated client: {e}")
        return None


def get_box_diagnostics(user_email: str) -> dict:
    """
    Get comprehensive Box connection diagnostics.
    
    Returns:
        Dictionary with diagnostic information
    """
    read_users, write_users, decrypt_token, encrypt_token = _get_web_app_funcs()
    diagnostics = {
        "connected": False,
        "has_access_token": False,
        "has_refresh_token": False,
        "has_expires_at": False,
        "token_expires_soon": False,
        "write_scope_ok": False,
        "write_scope_verified_at": None,
        "last_scope_error": None,
        "auth_flow_type": "OAuth 2.0 (3-legged)",
        "client_id_configured": False,
        "client_secret_configured": False,
        "sdk_available": BOXSDK_AVAILABLE,
        "status": "not_connected"
    }
    
    if not BOXSDK_AVAILABLE:
        diagnostics["status"] = "sdk_missing"
        return diagnostics
    
    BOX_CLIENT_ID, BOX_CLIENT_SECRET = get_box_credentials()
    diagnostics["client_id_configured"] = bool(BOX_CLIENT_ID)
    diagnostics["client_secret_configured"] = bool(BOX_CLIENT_SECRET)
    
    users = read_users()
    user_data = users.get(user_email.lower())
    if not user_data or "connected_apps" not in user_data or "box" not in user_data["connected_apps"]:
        diagnostics["status"] = "not_connected"
        return diagnostics
    
    diagnostics["connected"] = True
    box_config = user_data["connected_apps"]["box"]
    
    diagnostics["has_access_token"] = bool(box_config.get("access_token_encrypted"))
    diagnostics["has_refresh_token"] = bool(box_config.get("refresh_token_encrypted"))
    diagnostics["has_expires_at"] = bool(box_config.get("token_expires_at"))
    
    # Check token expiration
    token_expires_at = box_config.get("token_expires_at")
    if token_expires_at:
        current_time = int(time.time())
        time_until_expiry = token_expires_at - current_time
        diagnostics["token_expires_soon"] = time_until_expiry < 120
        diagnostics["token_expires_in_seconds"] = time_until_expiry
    
    # Check write scope status
    diagnostics["write_scope_ok"] = box_config.get("box_write_scope_ok", False)
    diagnostics["write_scope_verified_at"] = box_config.get("box_write_verified_at")
    diagnostics["last_scope_error"] = box_config.get("box_write_scope_error")
    diagnostics["needs_scope_update"] = box_config.get("needs_scope_update", False)
    diagnostics["needs_reauth"] = box_config.get("needs_reauth", False)
    
    # Determine overall status
    if diagnostics["needs_scope_update"] or (not diagnostics["write_scope_ok"] and diagnostics["last_scope_error"]):
        diagnostics["status"] = "needs_scopes"
    elif diagnostics["needs_reauth"]:
        diagnostics["status"] = "needs_reauth"
    elif not diagnostics["has_refresh_token"]:
        diagnostics["status"] = "missing_refresh_token"
    elif diagnostics["write_scope_ok"]:
        diagnostics["status"] = "ready"
    else:
        diagnostics["status"] = "unknown"
    
    return diagnostics


def verify_write_scope(user_email: str, force_check: bool = False) -> Tuple[bool, Optional[str]]:
    read_users, write_users, decrypt_token, encrypt_token = _get_web_app_funcs()
    """
    Verify that Box token has write permissions by attempting to create a test folder.
    Results are cached for 24 hours to avoid spamming Box API.
    
    Args:
        user_email: User email
        force_check: If True, bypass cache and check again
        
    Returns:
        Tuple of (has_write_scope: bool, error_message: Optional[str])
    """
    if not BOXSDK_AVAILABLE:
        return False, "boxsdk package not installed"
    
    users = read_users()
    user_data = users.get(user_email.lower())
    if not user_data or "connected_apps" not in user_data or "box" not in user_data["connected_apps"]:
        return False, "Box not connected"
    
    box_config = user_data["connected_apps"]["box"]
    
    # Check cache (24 hour validity) - but also check if status is already known to be bad
    if not force_check:
        # If we know scopes are bad, don't use cache
        if box_config.get("needs_scope_update") or box_config.get("box_write_scope_ok") == False:
            write_verified_at = box_config.get("box_write_verified_at")
            if write_verified_at:
                try:
                    verified_time = datetime.fromisoformat(write_verified_at)
                    # Only use cache if it's recent (within 1 hour) and we know it's bad
                    if datetime.now() - verified_time < timedelta(hours=1):
                        print(f"[Box] Write scope failed (cached, verified at {write_verified_at})")
                        return False, box_config.get("box_write_scope_error", "Write scope verification failed")
                except (ValueError, TypeError):
                    pass
        
        # If we know scopes are good, use cache if recent
        write_verified_at = box_config.get("box_write_verified_at")
        if write_verified_at:
            try:
                verified_time = datetime.fromisoformat(write_verified_at)
                if datetime.now() - verified_time < timedelta(hours=24):
                    write_scope_ok = box_config.get("box_write_scope_ok", False)
                    if write_scope_ok:
                        print(f"[Box] Write scope verified (cached, verified at {write_verified_at})")
                        return True, None
            except (ValueError, TypeError):
                pass  # Invalid timestamp, re-check
    
    # Perform actual check
    print(f"[Box] Verifying write scope...")
    client = get_authenticated_client(user_email)
    if not client:
        error_msg = "Failed to create authenticated client"
        _update_write_scope_cache(user_email, False, error_msg)
        return False, error_msg
    
    try:
        # Get root folder
        root_folder = client.folder('0')
        
        # Try to create a test folder with a more descriptive name
        test_folder_name = "PhiAI__scope_test__do_not_delete"
        
        # Check if test folder already exists
        try:
            items = list(root_folder.get_items())
            existing_test_folder = None
            for item in items:
                if item.type == 'folder' and item.name == test_folder_name:
                    existing_test_folder = item
                    break
            
            if existing_test_folder:
                # Test folder exists, try to delete it (also requires write scope)
                try:
                    existing_test_folder.delete()
                    print(f"[Box] Deleted existing test folder")
                except BoxAPIException as e:
                    if e.status == 403:
                        # Can't delete, but folder exists - try to create again to verify write
                        pass
                    else:
                        raise
            
            # Create test folder
            test_folder = root_folder.create_subfolder(test_folder_name)
            print(f"[Box] Created test folder: {test_folder.id}")
            
            # Delete test folder (cleanup)
            try:
                test_folder.delete()
                print(f"[Box] Deleted test folder (cleanup)")
            except Exception as cleanup_err:
                print(f"[Box] Warning: Could not delete test folder: {cleanup_err}")
                # Not critical - folder exists but is hidden
            
            # Success - write scope verified
            _update_write_scope_cache(user_email, True, None)
            print(f"[Box] Write scope verified successfully")
            return True, None
            
        except BoxAPIException as e:
            if e.status == 403:
                error_msg = str(e)
                if "insufficient_scope" in error_msg.lower() or "requires higher privileges" in error_msg.lower():
                    detailed_error = (
                        "Box token lacks write permissions (insufficient_scope). "
                        "This means your Box app is not configured with the required scopes. "
                        "See Settings → Connected Apps → Box for detailed fix instructions."
                    )
                    # Mark as needing scope update
                    users = read_users()
                    if user_email.lower() in users:
                        if "connected_apps" not in users[user_email.lower()]:
                            users[user_email.lower()]["connected_apps"] = {}
                        if "box" not in users[user_email.lower()]["connected_apps"]:
                            users[user_email.lower()]["connected_apps"]["box"] = {}
                        users[user_email.lower()]["connected_apps"]["box"]["needs_scope_update"] = True
                        users[user_email.lower()]["connected_apps"]["box"]["box_last_scope_error"] = "insufficient_scope"
                        write_users(users)
                    
                    _update_write_scope_cache(user_email, False, detailed_error)
                    return False, detailed_error
                else:
                    _update_write_scope_cache(user_email, False, error_msg)
                    return False, error_msg
            else:
                error_msg = f"Box API error during scope verification: {e}"
                _update_write_scope_cache(user_email, False, error_msg)
                return False, error_msg
                
    except Exception as e:
        error_msg = f"Exception during write scope verification: {e}"
        print(f"[Box] {error_msg}")
        _update_write_scope_cache(user_email, False, error_msg)
        return False, error_msg


def _update_write_scope_cache(user_email: str, has_scope: bool, error_msg: Optional[str]):
    """Update write scope verification cache in user data."""
    read_users, write_users, decrypt_token, encrypt_token = _get_web_app_funcs()
    try:
        users = read_users()
        if user_email.lower() in users:
            if "connected_apps" not in users[user_email.lower()]:
                users[user_email.lower()]["connected_apps"] = {}
            if "box" not in users[user_email.lower()]["connected_apps"]:
                users[user_email.lower()]["connected_apps"]["box"] = {}
            
            users[user_email.lower()]["connected_apps"]["box"]["box_write_verified_at"] = datetime.now().isoformat()
            users[user_email.lower()]["connected_apps"]["box"]["box_write_scope_ok"] = has_scope
            if error_msg:
                users[user_email.lower()]["connected_apps"]["box"]["box_write_scope_error"] = error_msg
            else:
                users[user_email.lower()]["connected_apps"]["box"].pop("box_write_scope_error", None)
            
            write_users(users)
    except Exception as e:
        print(f"[Box] Failed to update write scope cache: {e}")
