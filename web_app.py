import csv
import json
import os
import re
import secrets
import shutil
import smtplib
import ssl
import hashlib
import math
import subprocess
import sys
import threading
import time
import base64
import uuid
import warnings
import requests
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify, Response, stream_with_context
from urllib.parse import quote as url_encode
from werkzeug.security import generate_password_hash, check_password_hash
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, PatternMatchingEventHandler
from typing import Any, Optional

# Load .env file if python-dotenv is available
# override=True ensures .env file values take precedence over existing env vars
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass  # python-dotenv not installed, will use environment variables only

# Suppress google.api_core Python version warnings (avoid touching site-packages)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    module=r"google\\.api_core\\._python_version_support"
)

# ----------------------------
# Paths
# ----------------------------
ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
ENROLL_DIR = ROOT / "enroll"
STATIC_DIR = ROOT / "static"
UPLOAD_JOBS_DIR = OUTPUT_DIR / "jobs"
MEETING_JOBS_DIR = UPLOAD_JOBS_DIR / "meetings"
SPEAKER_PROFILES_JSON = OUTPUT_DIR / "speaker_profiles.json"

USERS_CSV = INPUT_DIR / "users.csv"     # first,last,email,password_hash,organizations_json,username,connected_apps_json
EMAILS_CSV = INPUT_DIR / "emails.csv"   # first,last,email (used by pipeline)
RESET_TOKENS_JSON = ROOT / "reset_tokens.json"  # email -> {token, expires}
MEETINGS_JSON = OUTPUT_DIR / "meetings.json"  # List of processed meetings with metadata
ORGANIZATIONS_JSON = ROOT / "organizations.json"  # Organizations and their members
ORGANIZATIONS_DIRECTORY_JSON = ROOT / "organizations_directory.json"  # Organization directory with details (name, abbrev, address, type, popularity)
CHAT_SESSIONS_JSON = OUTPUT_DIR / "chat_sessions.json"  # Chat sessions: {user_email: [sessions]}
CHAT_MESSAGES_JSON = OUTPUT_DIR / "chat_messages.json"  # Chat messages: {session_id: [messages]}
VOCABULARY_JSON = ROOT / "vocabulary.json"  # Custom vocabulary: {user_email: [vocab_entries]}

# Organization types and their role options
ORGANIZATION_TYPES = {
    "company": {
        "name": "Company",
        "roles": [
            "Founder", "CEO", "CTO", "CFO", "COO", "President", "Vice President",
            "Director", "Senior Director", "Manager", "Senior Manager", "Team Lead",
            "Senior Engineer", "Engineer", "Software Engineer", "Product Manager",
            "Project Manager", "Designer", "Marketing Manager", "Sales Manager",
            "HR Manager", "Operations Manager", "Business Analyst", "Data Analyst",
            "Consultant", "Advisor", "Intern", "Employee", "Other"
        ]
    },
    "school": {
        "name": "School/University",
        "roles": [
            "Student", "Student Athlete", "Graduate Student", "Undergraduate Student",
            "Professor", "Associate Professor", "Assistant Professor", "Lecturer",
            "Teaching Assistant (TA)", "Research Assistant (RA)", "Postdoc",
            "Department Head", "Dean", "Administrator", "Staff", "Alumni", "Other"
        ]
    },
    "organization": {
        "name": "Organization",
        "roles": [
            "Founder", "President", "Vice President", "Director", "Manager",
            "Member", "Volunteer", "Board Member", "Advisor", "Other"
        ]
    }
}

CONFIG_PATH = ROOT / "config.json"

ALLOWED_UPLOAD_EXT = {".wav", ".m4a", ".mp3", ".mp4", ".mov", ".aac", ".flac", ".ogg", ".webm"}
WATCH_ALLOWED_EXT = {".m4a", ".wav", ".mp3", ".mp4", ".mov"}

DEFAULT_CONFIG = {
    "watch_dir": r"C:\Users\bjones25\Box\Meetings\Drop",
    "speakers_expected": None,      # None = auto-detect
    "stable_seconds": 6,
    "stable_checks": 4,
    "min_speaker_seconds": 6.0,     # used by email_named_script.py
    "min_speaker_words": 20,
}

# ----------------------------
# Flask
# ----------------------------
app = Flask(__name__, static_folder=STATIC_DIR, template_folder=TEMPLATES)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# Context processor to make waves_debug available to all templates
@app.context_processor
def inject_waves_debug():
    return dict(waves_debug=os.getenv("WAVES_DEBUG") == "1")

# Context processor for common template variables (current user + Ask Phi availability)
@app.context_processor
def inject_common_context():
    cu = current_user()
    phi_available = False
    phi_error = None
    if cu:
        try:
            from integrations.ollama_client import get_ollama_status_cached
            phi_available, phi_error = get_ollama_status_cached()
        except Exception:
            phi_available, phi_error = False, None
    return dict(current_user=cu, phi_available=phi_available, phi_error=phi_error)

# ----------------------------
# Helper functions
# ----------------------------
def ensure_dirs():
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    ENROLL_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    MEETING_JOBS_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return DEFAULT_CONFIG.copy()
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        # Merge with defaults in case new keys are added
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()


def load_speaker_profiles() -> dict[str, dict]:
    """
    Global speaker profiles for cross-meeting speaker memory.
    Stored at output/speaker_profiles.json as {enrollment_key: {display_name, ...}, ...}
    """
    try:
        if not SPEAKER_PROFILES_JSON.exists():
            return {}
        data = json.loads(SPEAKER_PROFILES_JSON.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def save_speaker_profiles(profiles: dict[str, dict]) -> None:
    try:
        SPEAKER_PROFILES_JSON.parent.mkdir(parents=True, exist_ok=True)
        SPEAKER_PROFILES_JSON.write_text(json.dumps(profiles, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _slugify_speaker_key(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 _,-]+", "", s)
    s = s.replace(" ", "_")
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "speaker"


def ensure_speaker_profile(label_name: str) -> str:
    """
    Ensure a global profile exists for this display name and return its enrollment_key.
    If the label maps to a known user, prefer their username as the key (stable).
    Otherwise generate a slug key and keep a mapping to the original display name.
    """
    display_name = (label_name or "").strip()
    if not display_name:
        return ""

    # If this label matches a known user, prefer username so it works with participant filtering.
    username = _resolve_username_for_label(display_name)
    key = (username or "").strip().lower()
    if not key:
        key = _slugify_speaker_key(display_name)

    profiles = load_speaker_profiles()
    existing = profiles.get(key) if isinstance(profiles, dict) else None
    now = datetime.now().isoformat()
    if not isinstance(existing, dict):
        profiles[key] = {
            "display_name": display_name,
            "linked_username": username,
            "created_at": now,
            "updated_at": now,
            "evidence": [],
        }
        save_speaker_profiles(profiles)
        return key

    # Update display_name if it changed (keep latest)
    if display_name and existing.get("display_name") != display_name:
        existing["display_name"] = display_name
    existing["linked_username"] = username or existing.get("linked_username")
    existing["updated_at"] = now
    profiles[key] = existing
    save_speaker_profiles(profiles)
    return key


def _append_speaker_profile_evidence(enrollment_key: str, meeting_id: str, raw_speaker: str) -> None:
    try:
        key = (enrollment_key or "").strip().lower()
        if not key:
            return
        profiles = load_speaker_profiles()
        prof = profiles.get(key) if isinstance(profiles, dict) else None
        if not isinstance(prof, dict):
            return
        ev = prof.get("evidence", [])
        if not isinstance(ev, list):
            ev = []
        ev.append({
            "meeting_id": str(meeting_id),
            "raw_speaker": str(raw_speaker),
            "updated_at": datetime.now().isoformat(),
        })
        # cap list size
        prof["evidence"] = ev[-50:]
        prof["updated_at"] = datetime.now().isoformat()
        profiles[key] = prof
        save_speaker_profiles(profiles)
    except Exception:
        return
def valid_email(email: str) -> bool:
    email = (email or "").strip()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))

def get_audio_duration(file_path: Path) -> float:
    """Get audio file duration in seconds using ffprobe"""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path)
            ],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return 0.0

def enrollment_file_matches_user(filename: str, user: dict) -> bool:
    """Check if an enrollment filename matches a user (supports both firstname,lastname and username formats)"""
    filename_lower = filename.lower()
    username = user.get("username", "").strip().lower()
    first = user.get("first", "").strip().lower()
    last = user.get("last", "").strip().lower()
    name_prefix = f"{first},{last}".lower() if first and last else ""
    
    # Get the base name (without extension and without number suffix like (2), (3))
    # This handles files like "bobby,jones.webm", "bobby,jones(2).webm", "bobbyjones.webm", etc.
    base_name = filename_lower
    if '.' in base_name:
        base_name = base_name.rsplit('.', 1)[0]  # Remove extension
    # Remove number suffix like (2), (3) for matching
    base_name_normalized = re.sub(r'\(\d+\)$', '', base_name).strip()
    
    # Check both firstname,lastname format and username format (backward compatibility)
    if name_prefix:
        # Check if base name (normalized) matches firstname,lastname
        name_prefix_normalized = name_prefix
        if base_name_normalized == name_prefix_normalized or base_name_normalized.startswith(name_prefix_normalized):
            return True
        # Also check if base name without comma matches username derived from firstname,lastname
        base_without_comma = base_name_normalized.replace(',', '').replace(' ', '')
        name_prefix_without_comma = name_prefix_normalized.replace(',', '').replace(' ', '')
        if base_without_comma == name_prefix_without_comma:
            return True
    
    if username:
        # Check if base name (normalized) matches username
        if base_name_normalized == username or base_name_normalized.startswith(username):
            return True
        # Also check if base name with comma matches firstname,lastname derived from username
        # (This is less common but for backward compatibility)
        if ',' in base_name_normalized:
            base_without_comma = base_name_normalized.replace(',', '').replace(' ', '')
            if base_without_comma == username:
                return True
    
    return False

# ----------------------------
# Organizations management
# ----------------------------
def load_organizations() -> dict:
    """Load organizations from JSON file. Returns dict: org_name -> {type, members: [email, ...]}"""
    if not ORGANIZATIONS_JSON.exists():
        return {}
    try:
        return json.loads(ORGANIZATIONS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_organizations(orgs: dict):
    """Save organizations to JSON file."""
    ORGANIZATIONS_JSON.write_text(json.dumps(orgs, indent=2), encoding="utf-8")

def load_organizations_directory() -> list:
    """Load organization directory. Returns list of org dicts with name, abbreviation, address, type, popularity."""
    if not ORGANIZATIONS_DIRECTORY_JSON.exists():
        # Initialize with default organizations
        default_orgs = [
            {
                "name": "Massachusetts Institute of Technology",
                "abbreviation": "MIT",
                "address": "77 Massachusetts Ave, Cambridge, MA 02139",
                "type": "school",
                "popularity": 100
            },
            {
                "name": "Link Studio",
                "abbreviation": "",
                "address": "One Kendall Square Building 200 - Suite B2201 Cambridge, MA 02139",
                "type": "company",
                "popularity": 50
            },
            {
                "name": "PhiAI",
                "abbreviation": "",
                "address": "189 vassar street, cambridge mass, 02139, room 3102",
                "type": "company",
                "popularity": 50
            }
        ]
        save_organizations_directory(default_orgs)
        return default_orgs
    try:
        return json.loads(ORGANIZATIONS_DIRECTORY_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_organizations_directory(orgs: list):
    """Save organization directory to JSON file."""
    ORGANIZATIONS_DIRECTORY_JSON.write_text(json.dumps(orgs, indent=2), encoding="utf-8")

def search_organizations_directory(query: str, org_type: str = None) -> list:
    """Search organization directory by name or abbreviation. Returns list of matching orgs sorted by popularity."""
    query = query.lower().strip()
    if not query:
        return []
    
    directory = load_organizations_directory()
    matches = []
    
    for org in directory:
        if org_type and org.get("type") != org_type:
            continue
        
        name_lower = org.get("name", "").lower()
        abbrev_lower = (org.get("abbreviation") or "").lower()
        
        # Match if query is in name or abbreviation
        if query in name_lower or query in abbrev_lower:
            matches.append(org)
    
    # Sort by popularity (descending), then by name
    matches.sort(key=lambda x: (-x.get("popularity", 0), x.get("name", "").lower()))
    return matches

def add_organization_to_directory(name: str, abbreviation: str, address: str, org_type: str) -> dict:
    """Add a new organization to the directory. Returns the created org dict."""
    directory = load_organizations_directory()
    
    # Check if org already exists (by name and address)
    for org in directory:
        if org.get("name", "").lower() == name.lower() and org.get("address", "").lower() == address.lower():
            return org  # Return existing
    
    new_org = {
        "name": name,
        "abbreviation": abbreviation,
        "address": address,
        "type": org_type,
        "popularity": 0
    }
    directory.append(new_org)
    save_organizations_directory(directory)
    return new_org

def get_organization_members(org_name: str) -> list:
    """Get list of member emails for an organization."""
    orgs = load_organizations()
    org = orgs.get(org_name, {})
    return org.get("members", [])

def add_user_to_organization(org_name: str, org_type: str, user_email: str):
    """Add a user to an organization. Creates org if it doesn't exist."""
    orgs = load_organizations()
    if org_name not in orgs:
        orgs[org_name] = {"type": org_type, "members": []}
    if user_email.lower() not in [m.lower() for m in orgs[org_name]["members"]]:
        orgs[org_name]["members"].append(user_email)
    save_organizations(orgs)

def remove_user_from_organization(org_name: str, user_email: str):
    """Remove a user from an organization."""
    orgs = load_organizations()
    if org_name in orgs:
        orgs[org_name]["members"] = [m for m in orgs[org_name]["members"] if m.lower() != user_email.lower()]
        if not orgs[org_name]["members"]:
            # Remove empty organization
            del orgs[org_name]
        save_organizations(orgs)

def search_organizations(query: str) -> list:
    """Search organizations by name. Returns list of matching org names (legacy function for member search)."""
    query = query.lower().strip()
    if not query:
        return []
    orgs = load_organizations()
    matches = []
    for org_name in orgs.keys():
        if query in org_name.lower():
            matches.append(org_name)
    return sorted(matches)

# ----------------------------
# Users management
# ----------------------------
def init_users_csv():
    if not USERS_CSV.exists():
        with open(USERS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["first", "last", "email", "password_hash", "organizations_json", "username", "connected_apps_json", "receive_meeting_emails"])
            w.writeheader()
    else:
        # Migrate existing CSV to include organizations and username fields
        with open(USERS_CSV, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            
            # Check if migration needed
            if "organizations_json" not in fieldnames or "position" in fieldnames or "username" not in fieldnames or "receive_meeting_emails" not in fieldnames:
                users = {}
                f.seek(0)
                for row in reader:
                    email = (row.get("email") or "").strip().lower()
                    if not email:
                        continue
                    
                    # Migrate: convert position to organizations
                    organizations = []
                    if "position" in row:
                        position = (row.get("position") or "").strip()
                        founders = ["bobbynedjones19@gmail.com", "jplukish@mit.edu", "cmagnano@mit.edu", "tgp@mit.edu"]
                        
                        # Set up founder organizations
                        if email.lower() in founders:
                            organizations = [
                                {"name": "PhiAI", "type": "company", "role": "Founder"},
                                {"name": "Link Studio", "type": "company", "role": "Intern"},
                                {"name": "Massachusetts Institute of Technology", "type": "school", "role": "Student Athlete"}
                            ]
                            # Add to organizations.json
                            for org in organizations:
                                add_user_to_organization(org["name"], org["type"], email)
                        elif position:
                            # Default to single org with position (will be migrated to proper org later)
                            organizations = [{"name": "My Company", "type": "company", "role": position}]
                            add_user_to_organization("My Company", "company", email)
                    
                    # Generate username from existing data or use default
                    username = (row.get("username") or "").strip().lower()
                    if not username:
                        first = (row.get("first") or "").strip().lower()
                        last = (row.get("last") or "").strip().lower()
                        # Set default usernames for founders
                        if email.lower() == "bobbynedjones19@gmail.com":
                            username = "bobbyjones"
                        elif email.lower() == "jplukish@mit.edu":
                            username = "jameslukish"
                        elif email.lower() == "cmagnano@mit.edu":
                            username = "cartermagnano"
                        elif email.lower() == "tgp@mit.edu":
                            username = "theoperkins"
                        else:
                            # Default: first + last (no spaces, lowercase)
                            username = (first + last).replace(" ", "").lower()
                    
                    users[email] = {
                        "first": (row.get("first") or "").strip(),
                        "last": (row.get("last") or "").strip(),
                        "email": email,
                        "password_hash": (row.get("password_hash") or "").strip(),
                        "organizations": organizations,
                        "username": username,
                        "connected_apps": {},
                        "receive_meeting_emails": True,  # Default to True for existing users
                    }
                write_users(users)

def read_users() -> dict:
    """Read users from CSV. Returns dict: email -> user_data (includes organizations list)."""
    init_users_csv()
    users = {}
    with open(USERS_CSV, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            email = (row.get("email") or "").strip().lower()
            if not email:
                continue
            
            # Parse organizations from JSON string
            organizations = []
            org_json = (row.get("organizations_json") or "").strip()
            if org_json:
                try:
                    organizations = json.loads(org_json)
                except Exception:
                    organizations = []
            
            # Backward compatibility: if no organizations, check for position
            if not organizations and "position" in row:
                position = (row.get("position") or "").strip()
                if position:
                    organizations = [{"name": "My Company", "type": "company", "role": position}]
            
            # Get username, generate default if missing
            username = (row.get("username") or "").strip().lower()
            if not username:
                first = (row.get("first") or "").strip().lower()
                last = (row.get("last") or "").strip().lower()
                # Set default usernames for founders
                if email.lower() == "bobbynedjones19@gmail.com":
                    username = "bobbyjones"
                elif email.lower() == "jplukish@mit.edu":
                    username = "jameslukish"
                elif email.lower() == "cmagnano@mit.edu":
                    username = "cartermagnano"
                elif email.lower() == "tgp@mit.edu":
                    username = "theoperkins"
                else:
                    # Default: first + last (no spaces, lowercase)
                    username = (first + last).replace(" ", "").lower()
            
            # Parse connected apps from JSON string
            connected_apps = {}
            connected_apps_json = (row.get("connected_apps_json") or "").strip()
            if connected_apps_json:
                try:
                    connected_apps = json.loads(connected_apps_json)
                except Exception:
                    connected_apps = {}
            
            # Get receive_meeting_emails preference, default to True
            receive_emails_str = (row.get("receive_meeting_emails") or "true").strip().lower()
            receive_meeting_emails = receive_emails_str == "true"
            
            users[email] = {
                "first": (row.get("first") or "").strip(),
                "last": (row.get("last") or "").strip(),
                "email": email,
                "password_hash": (row.get("password_hash") or "").strip(),
                "organizations": organizations,
                "username": username,
                "connected_apps": connected_apps,
                "receive_meeting_emails": receive_meeting_emails,
            }
    return users

def write_users(users: dict):
    """Write users to CSV. Stores organizations as JSON string."""
    rows = sorted(users.values(), key=lambda u: (u["last"].lower(), u["first"].lower(), u["email"]))
    with open(USERS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["first", "last", "email", "password_hash", "organizations_json", "username", "connected_apps_json", "receive_meeting_emails"])
        w.writeheader()
        for row in rows:
            # Convert organizations list to JSON string
            organizations = row.get("organizations", [])
            org_json = json.dumps(organizations) if organizations else ""
            
            # Convert connected apps dict to JSON string
            connected_apps = row.get("connected_apps", {})
            connected_apps_json = json.dumps(connected_apps) if connected_apps else ""
            
            # Get receive_meeting_emails preference, default to True
            receive_meeting_emails = row.get("receive_meeting_emails", True)
            if isinstance(receive_meeting_emails, bool):
                receive_meeting_emails_str = "true" if receive_meeting_emails else "false"
            else:
                receive_meeting_emails_str = str(receive_meeting_emails).lower()
            
            w.writerow({
                "first": row["first"],
                "last": row["last"],
                "email": row["email"],
                "password_hash": row["password_hash"],
                "organizations_json": org_json,
                "username": row.get("username", "").lower(),
                "connected_apps_json": connected_apps_json,
                "receive_meeting_emails": receive_meeting_emails_str,
            })

def sync_emails_csv(users: dict):
    """Create/overwrite input/emails.csv with first,last,email (no passwords)."""
    with open(EMAILS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["first", "last", "email"])
        w.writeheader()
        for u in sorted(users.values(), key=lambda x: (x["last"].lower(), x["first"].lower(), x["email"])):
            w.writerow({"first": u["first"], "last": u["last"], "email": u["email"]})

def get_encryption_key():
    """Get or generate encryption key for tokens"""
    key_str = os.getenv("ENCRYPTION_KEY")
    if not key_str:
        # Generate a key (store this securely!)
        try:
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            print(f"⚠️  Generated encryption key. Add to .env: ENCRYPTION_KEY={key.decode()}")
            return key
        except ImportError:
            print("⚠️  cryptography not installed. Install with: pip install cryptography")
            return None
    return key_str.encode()

def encrypt_token(token: str) -> str:
    """Encrypt access token before storing"""
    try:
        from cryptography.fernet import Fernet
        key = get_encryption_key()
        if not key:
            return token  # Fallback
        f = Fernet(key)
        return f.encrypt(token.encode()).decode()
    except Exception as e:
        print(f"Error encrypting token: {e}")
        return token  # Fallback (not secure, but prevents crashes)

def decrypt_token(encrypted_token: str) -> str:
    """Decrypt access token"""
    try:
        from cryptography.fernet import Fernet
        key = get_encryption_key()
        if not key:
            return encrypted_token  # Fallback
        f = Fernet(key)
        return f.decrypt(encrypted_token.encode()).decode()
    except Exception as e:
        print(f"Error decrypting token: {e}")
        return encrypted_token  # Fallback

def current_user():
    email = session.get("user_email")
    if not email:
        return None
    users = read_users()
    return users.get(email)

def require_login():
    if not session.get("user_email"):
        return False
    return True

# ----------------------------
# Reset tokens
# ----------------------------
def load_reset_tokens() -> dict:
    """Load reset tokens from JSON file."""
    if RESET_TOKENS_JSON.exists():
        try:
            return json.loads(RESET_TOKENS_JSON.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_reset_tokens(tokens: dict):
    """Save reset tokens to JSON file."""
    RESET_TOKENS_JSON.write_text(json.dumps(tokens, indent=2), encoding="utf-8")

def create_reset_token(email: str) -> str:
    """Create a reset token for an email. Returns the token."""
    token = secrets.token_urlsafe(32)
    tokens = load_reset_tokens()
    tokens[email.lower()] = {
        "token": token,
        "expires": (datetime.now() + timedelta(hours=1)).isoformat()
    }
    save_reset_tokens(tokens)
    return token

def verify_reset_token(email: str, token: str) -> bool:
    """Verify a reset token for an email. Returns True if valid."""
    tokens = load_reset_tokens()
    token_data = tokens.get(email.lower(), {})
    if not token_data:
        return False
    
    stored_token = token_data.get("token", "")
    expires_str = token_data.get("expires", "")
    
    if stored_token != token:
        return False
    
    try:
        expires = datetime.fromisoformat(expires_str)
        if datetime.now() > expires:
            return False
    except Exception:
        return False
    
    return True

# ----------------------------
# Email sending
# ----------------------------
def send_email(to_email: str, subject: str, body: str, attachments: list = None):
    """Send an email using SMTP from .env configuration."""
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    
    if not all([smtp_host, smtp_user, smtp_pass]):
        print(f"ERROR: SMTP not configured. Cannot send email to {to_email}")
        return False
    
    try:
        msg = EmailMessage()
        msg["From"] = f"Phi AI <{smtp_user}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        
        if attachments:
            for att in attachments:
                if isinstance(att, dict) and "content" in att and "filename" in att:
                    msg.add_attachment(
                        att["content"],
                        maintype=att.get("maintype", "application"),
                        subtype=att.get("subtype", "pdf"),
                        filename=att["filename"]
                    )
        
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"ERROR: Failed to send email to {to_email}: {e}")
        return False

# ----------------------------
# Meetings management
# ----------------------------
def load_meetings() -> list:
    """Load list of processed meetings."""
    if not MEETINGS_JSON.exists():
        return []
    try:
        return json.loads(MEETINGS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_meeting(meeting_data: dict):
    """Save meeting metadata to meetings.json."""
    meetings = load_meetings()
    meetings.append(meeting_data)
    # Sort by date (newest first)
    meetings.sort(key=lambda x: x.get("processed_at", ""), reverse=True)
    MEETINGS_JSON.write_text(json.dumps(meetings, indent=2), encoding="utf-8")

def update_meeting(meeting_id: str, updates: dict):
    """Update a meeting's metadata in meetings.json."""
    meetings = load_meetings()
    for i, meeting in enumerate(meetings):
        if meeting.get("id") == meeting_id:
            meetings[i].update(updates)
            MEETINGS_JSON.write_text(json.dumps(meetings, indent=2), encoding="utf-8")
            return True
    return False

def get_meeting(meeting_id: str) -> dict | None:
    """Get a meeting by ID."""
    meetings = load_meetings()
    for meeting in meetings:
        if meeting.get("id") == meeting_id:
            return meeting
    return None

def detect_unknown_speakers(meeting: dict) -> dict:
    """
    Detect unknown speakers in a meeting.
    Returns dict with:
    - has_unknown_speakers: bool
    - unknown_speakers: list of unknown speaker labels (e.g., ["Unknown Speaker 1", "Unknown Speaker 2"])
    - unknown_speaker_count: int
    """
    unknown_speakers = []
    has_unknown = False
    
    # Check transcript file for unknown / unlabeled speakers
    transcript_path = None
    if meeting.get("transcript_path"):
        transcript_path = ROOT / meeting["transcript_path"]
    elif meeting.get("id"):
        # Try standard paths
        transcript_path = OUTPUT_DIR / f"{meeting['id']}_named_script.txt"
    
    if transcript_path and transcript_path.exists():
        try:
            content = transcript_path.read_text(encoding="utf-8")
            # Find all "Unknown Speaker N" patterns (preferred)
            unknown_pattern = r"Unknown Speaker \d+"
            matches = re.findall(unknown_pattern, content)
            unknown_speakers = sorted(list(set(matches)))  # Unique, sorted

            # Fallback: if transcript is still diarization-labeled, surface those too
            if not unknown_speakers:
                diar_matches = re.findall(r"\bSPEAKER_\d+\b", content)
                diar_matches = sorted(list(set(diar_matches)))
                if diar_matches:
                    unknown_speakers = diar_matches

            # Legacy: plain "Unknown" label
            if "Unknown:" in content and "Unknown Speaker" not in content:
                if "Unknown" not in unknown_speakers:
                    unknown_speakers.append("Unknown")

            has_unknown = len(unknown_speakers) > 0
        except Exception as e:
            print(f"Warning: Could not read transcript to detect unknown speakers: {e}")
    
    # Filter out speakers that have already been labeled
    speaker_label_map = meeting.get("speaker_label_map", {})
    if speaker_label_map:
        # Remove any unknown speakers that have been labeled (mapped to non-unknown names)
        labeled_unknowns = set()
        for key, value in speaker_label_map.items():
            if isinstance(key, str) and key.startswith("Unknown Speaker"):
                # If the value is NOT an "Unknown Speaker" pattern, it's been labeled
                if isinstance(value, str) and not value.startswith("Unknown Speaker"):
                    labeled_unknowns.add(key)
        
        # Remove labeled speakers from the list
        unknown_speakers = [s for s in unknown_speakers if s not in labeled_unknowns]
        
        # Also check if there are any unlabeled entries in the map
        for key, value in speaker_label_map.items():
            if isinstance(key, str) and key.startswith("Unknown Speaker"):
                # If value is still "Unknown Speaker" or empty, it's unlabeled
                if not value or (isinstance(value, str) and value.startswith("Unknown Speaker")):
                    if key not in unknown_speakers:
                        unknown_speakers.append(key)
                        has_unknown = True
    
    has_unknown = len(unknown_speakers) > 0
    
    return {
        "has_unknown_speakers": has_unknown,
        "unknown_speakers": sorted(unknown_speakers),
        "unknown_speaker_count": len(unknown_speakers)
    }


def _merge_consecutive_script_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    last: Optional[dict] = None
    for r in rows:
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        if last and last.get("speaker_name") == r.get("speaker_name"):
            last["text"] = (str(last.get("text") or "").strip() + " " + txt).strip()
            last["end"] = r.get("end")
        else:
            last = dict(r)
            out.append(last)
    return out


def _seed_label_map_from_named_json(meeting_id: str) -> dict[str, str]:
    """Attempt to seed diarization speaker labels from output/<id>_named_script.json."""
    seeded: dict[str, str] = {}
    named_json_path = OUTPUT_DIR / f"{meeting_id}_named_script.json"
    if not named_json_path.exists():
        return seeded
    try:
        rows = json.loads(named_json_path.read_text(encoding="utf-8"))
        for r in rows:
            diar = (r.get("diarization_speaker") or "").strip()
            spk = (r.get("speaker_name") or "").strip()
            if diar and spk and spk != "Unknown":
                seeded[diar] = spk
    except Exception:
        return {}
    return seeded


def _unknown_map_from_utterances(utterances: list[dict]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Return (unknown_by_raw, raw_by_unknown, speakers_in_order)."""
    speakers_in_order: list[str] = []
    for u in utterances:
        s = (u.get("speaker") or "").strip()
        if not s:
            continue
        if s not in speakers_in_order:
            speakers_in_order.append(s)
    unknown_by_raw: dict[str, str] = {raw: f"Unknown Speaker {i+1}" for i, raw in enumerate(speakers_in_order)}
    raw_by_unknown: dict[str, str] = {v: k for k, v in unknown_by_raw.items()}
    return unknown_by_raw, raw_by_unknown, speakers_in_order


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _utterance_id_from_fields(start: float, end: float, speaker: str, text: str) -> str:
    payload = f"{float(start):.3f}|{float(end):.3f}|{(speaker or '').strip()}|{(text or '').strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _utterance_id_for_item(u: dict) -> str:
    return _utterance_id_from_fields(
        float(u.get("start", 0.0) or 0.0),
        float(u.get("end", 0.0) or 0.0),
        (u.get("speaker") or "").strip(),
        (u.get("text") or "").strip(),
    )


def _derived_utterance_id(base_id: str, part: str, split_time: float, text: str) -> str:
    payload = f"{base_id}|{part}|{float(split_time):.3f}|{(text or '').strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _utterance_overrides_map(meeting: dict) -> dict[str, str]:
    """Return utterance_id -> speaker_display override."""
    out: dict[str, str] = {}
    items = meeting.get("utterance_overrides", [])
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        uid = (it.get("utterance_id") or "").strip()
        if not uid:
            continue
        if (it.get("type") or "").strip() != "speaker_display_override":
            continue
        name = it.get("speaker_display")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue
        out[uid] = name
    return out


def _utterance_splits_map(meeting: dict) -> dict[str, dict]:
    """Return source utterance_id -> split object. Supports both old format (single split) and new format (multiple splits)."""
    out: dict[str, dict] = {}
    items = meeting.get("utterance_splits", [])
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        uid = (it.get("utterance_id") or "").strip()
        if not uid:
            continue
        
        # Convert old format to new format if needed (backward compatibility)
        if "splits" not in it:
            # Old format: single split
            split_time = it.get("split_time")
            split_word_index = it.get("split_word_index")
            if split_time is not None or split_word_index is not None:
                splits_array = [{
                    "word_index": int(split_word_index) if split_word_index is not None else 0,
                    "split_time": float(split_time) if split_time is not None else 0.0,
                    "speaker_display": it.get("part_b", {}).get("speaker_display") if isinstance(it.get("part_b"), dict) else None,
                }]
                it["splits"] = splits_array
                # Keep part_a for backward compatibility
                if "part_a" not in it:
                    it["part_a"] = {}
        
        out[uid] = it
    return out


def _split_text_by_word_index(text: str, idx: int) -> tuple[str, str]:
    words = (text or "").strip().split()
    if len(words) < 2:
        return (text or "").strip(), ""
    i = int(idx)
    i = max(1, min(len(words) - 1, i))
    return " ".join(words[:i]).strip(), " ".join(words[i:]).strip()


def _choose_split_word_index(text: str, start: float, end: float, split_time: float) -> int:
    words = (text or "").strip().split()
    if len(words) < 2:
        return 1
    dur = max(0.001, float(end) - float(start))
    p = _clamp((float(split_time) - float(start)) / dur, 0.0, 1.0)
    idx = int(math.floor(p * len(words)))
    idx = max(1, min(len(words) - 1, idx))
    return idx


def _confidence_percent_for_utterance(duration: float, prev_same: bool, next_same: bool) -> int:
    """
    Approximate diarization confidence using duration + local speaker consistency.
    Returns integer percent 0-100.
    """
    d = max(0.0, float(duration))
    base = 0.55
    dur_bonus = _clamp(math.log1p(d) / 4.0, 0.0, 0.25)
    context_bonus = (0.10 if prev_same else 0.0) + (0.10 if next_same else 0.0)
    short_penalty = 0.15 if d < 1.2 else 0.0
    conf = _clamp(base + dur_bonus + context_bonus - short_penalty, 0.25, 0.95)
    return int(round(conf * 100))


def _effective_utterances_for_meeting(meeting_id: str, meeting: dict) -> list[dict]:
    """
    Read output/<meeting_id>_utterances.json and apply meeting.utterance_splits and
    meeting.utterance_overrides to produce an "effective" utterance list.

    Each returned item includes:
      - start/end/speaker/text (speaker is raw diarization label)
      - utterance_id (stable id used for overrides)
      - source_utterance_id (original utterance id from the source utterances.json)
      - speaker_display_override (optional, from overrides or split parts)
    """
    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    if not utterances_path.exists():
        return []
    try:
        src = json.loads(utterances_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(src, list):
        return []

    overrides = _utterance_overrides_map(meeting)
    splits = _utterance_splits_map(meeting)

    out: list[dict] = []
    for u in src:
        if not isinstance(u, dict):
            continue
        raw = (u.get("speaker") or "").strip()
        start = float(u.get("start", 0.0) or 0.0)
        end = float(u.get("end", 0.0) or 0.0)
        txt = (u.get("text") or "").strip()
        if not txt:
            continue

        uid = _utterance_id_from_fields(start, end, raw, txt)
        split_obj = splits.get(uid)
        if split_obj:
            # Get splits array (new format) or convert old format
            splits_array = split_obj.get("splits", [])
            if not isinstance(splits_array, list):
                # Old format: try to convert
                split_time = split_obj.get("split_time")
                split_word_index = split_obj.get("split_word_index")
                if split_time is not None:
                    splits_array = [{
                        "word_index": int(split_word_index) if split_word_index is not None else _choose_split_word_index(txt, start, end, float(split_time)),
                        "split_time": float(split_time),
                        "speaker_display": split_obj.get("part_b", {}).get("speaker_display") if isinstance(split_obj.get("part_b"), dict) else None,
                    }]
                else:
                    splits_array = []
            
            if splits_array:
                # Sort splits by word_index to ensure correct order
                splits_array = sorted(splits_array, key=lambda x: int(x.get("word_index", 0)))
                
                # Get words array if available
                words = u.get("words")
                word_list = txt.split() if not isinstance(words, list) else None
                
                # Create parts from splits
                parts = []
                prev_word_idx = 0
                prev_time = start
                
                for i, split_info in enumerate(splits_array):
                    word_idx = int(split_info.get("word_index", 0))
                    split_time = float(split_info.get("split_time", 0.0))
                    speaker_display = split_info.get("speaker_display")
                    
                    # Validate word index (word_idx is where to split BEFORE, so it should be between 1 and len(words))
                    txt_words = txt.split()
                    word_count = len(txt_words) if txt_words else 1
                    # Clamp to valid range but preserve the intended split point
                    original_word_idx = word_idx
                    word_idx = max(1, min(word_count - 1, word_idx))
                    if word_idx != original_word_idx:
                        # Log warning but continue with clamped value
                        print(f"[SPLIT] Warning: Clamped word_idx from {original_word_idx} to {word_idx}")
                    
                    # Split text by word index (split BEFORE word_idx, so words prev_word_idx to word_idx-1 go to this part)
                    if prev_word_idx < len(txt_words) and word_idx <= len(txt_words) and prev_word_idx < word_idx:
                        part_text = " ".join(txt_words[prev_word_idx:word_idx])
                    else:
                        part_text = ""
                    
                    # Calculate times - use split_time if available, otherwise calculate proportionally
                    if split_time > 0 and start < split_time < end:
                        part_start_time = prev_time
                        part_end_time = split_time
                    else:
                        # Proportional calculation based on word position
                        dur = end - start
                        word_count = len(txt_words) if txt_words else 1
                        if word_count > 0:
                            part_start_time = start + (prev_word_idx / word_count) * dur
                            part_end_time = start + (word_idx / word_count) * dur
                        else:
                            part_start_time = prev_time
                            part_end_time = split_time if split_time > 0 else end
                    
                    # Get words for this part (words from prev_word_idx to word_idx-1)
                    part_words = None
                    if isinstance(words, list) and len(words) > 0:
                        part_words = []
                        for idx, w in enumerate(words):
                            if prev_word_idx <= idx < word_idx:
                                part_words.append(w)
                        # Update times from word timestamps if available
                        if part_words:
                            part_start_time = float(part_words[0].get("start", part_start_time))
                            part_end_time = float(part_words[-1].get("end", part_end_time))
                        elif word_idx < len(words):
                            # Use the split point word's start time
                            part_end_time = float(words[word_idx].get("start", part_end_time))
                    
                    # Generate part ID
                    part_label = chr(65 + i)  # A, B, C, D, etc.
                    part_id = _derived_utterance_id(uid, part_label, part_end_time, part_text)
                    
                    # Get speaker display (first part uses part_a if available, others use split speaker_display)
                    if i == 0 and isinstance(split_obj.get("part_a"), dict):
                        part_speaker_display = split_obj["part_a"].get("speaker_display")
                    else:
                        part_speaker_display = speaker_display
                    
                    part_data = {
                        "start": part_start_time,
                        "end": part_end_time,
                        "speaker": raw,
                        "text": part_text,
                        "utterance_id": part_id,
                        "source_utterance_id": uid,
                        "speaker_display_override": (overrides.get(part_id) or (part_speaker_display or None)),
                    }
                    if part_words:
                        part_data["words"] = part_words
                    
                    if part_text:
                        parts.append(part_data)
                    
                    prev_word_idx = word_idx
                    prev_time = part_end_time
                
                # Add final part (from last split to end)
                txt_words = txt.split()
                if prev_word_idx < len(txt_words):
                    final_text = " ".join(txt_words[prev_word_idx:])
                    
                    # Calculate final part times
                    if isinstance(words, list) and len(words) > prev_word_idx:
                        final_words = words[prev_word_idx:]
                        if final_words:
                            final_start_time = float(final_words[0].get("start", prev_time))
                            final_end_time = float(final_words[-1].get("end", end))
                        else:
                            final_start_time = prev_time
                            final_end_time = end
                    else:
                        final_start_time = prev_time
                        final_end_time = end
                        final_words = None
                    
                    final_part_label = chr(65 + len(splits_array))  # Next letter after splits
                    final_part_id = _derived_utterance_id(uid, final_part_label, final_end_time, final_text)
                    
                    # Get speaker display for final part - use the last split's speaker_display if available
                    final_part_speaker_display = None
                    if splits_array:
                        last_split = splits_array[-1]
                        final_part_speaker_display = last_split.get("speaker_display")
                    
                    final_part_data = {
                        "start": final_start_time,
                        "end": final_end_time,
                        "speaker": raw,
                        "text": final_text,
                        "utterance_id": final_part_id,
                        "source_utterance_id": uid,
                        "speaker_display_override": (overrides.get(final_part_id) or (final_part_speaker_display or None)),
                    }
                    if final_words:
                        final_part_data["words"] = final_words
                    
                    if final_text:
                        parts.append(final_part_data)
                
                # Add all parts to output
                if parts:
                    out.extend(parts)
                    continue  # do not add original

        utterance_data = {
            "start": start,
            "end": end,
            "speaker": raw,
            "text": txt,
            "utterance_id": uid,
            "source_utterance_id": uid,
            "speaker_display_override": overrides.get(uid),
        }
        
        # Preserve word-level timestamps if available
        if "words" in u and isinstance(u["words"], list):
            utterance_data["words"] = u["words"]
        
        out.append(utterance_data)

    out.sort(key=lambda x: (float(x.get("start", 0.0) or 0.0), float(x.get("end", 0.0) or 0.0)))

    # Add approximate confidence after ordering
    for i, u in enumerate(out):
        raw = (u.get("speaker") or "").strip()
        prev_same = i > 0 and ((out[i - 1].get("speaker") or "").strip() == raw)
        next_same = i + 1 < len(out) and ((out[i + 1].get("speaker") or "").strip() == raw)
        dur = float(u.get("end", 0.0) or 0.0) - float(u.get("start", 0.0) or 0.0)
        u["speaker_confidence_percent"] = _confidence_percent_for_utterance(dur, prev_same, next_same)

    return out


def _build_labeled_script_from_utterances(meeting_id: str, meeting: dict, raw_label_overrides: dict[str, str]) -> dict[str, Any]:
    """
    Build labeled script assets from utterances.json + existing labels.
    Returns dict with paths + computed rows.
    """
    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    if not utterances_path.exists():
        raise FileNotFoundError(f"Utterances not found: {utterances_path}")
    utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
    if not isinstance(utterances, list):
        raise ValueError("utterances.json must be a list")

    unknown_by_raw, raw_by_unknown, speakers_in_order = _unknown_map_from_utterances(utterances)
    seeded = _seed_label_map_from_named_json(meeting_id)

    stored_map: dict[str, str] = meeting.get("speaker_label_map", {}) if isinstance(meeting.get("speaker_label_map"), dict) else {}
    stored_map_raw: dict[str, str] = {}
    for k, v in stored_map.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        key = k.strip()
        val = v.strip()
        if not val:
            continue
        if key in unknown_by_raw:
            stored_map_raw[key] = val
        elif key in raw_by_unknown:
            stored_map_raw[raw_by_unknown[key]] = val

    # Effective mapping: Unknown Speaker N defaults -> seeded names -> stored names -> API overrides
    effective: dict[str, str] = dict(unknown_by_raw)
    effective.update(seeded)
    effective.update(stored_map_raw)
    effective.update(raw_label_overrides)

    effective_utterances = _effective_utterances_for_meeting(meeting_id, meeting)

    rows: list[dict] = []
    for u in effective_utterances:
        raw = (u.get("speaker") or "").strip()
        start = float(u.get("start", 0.0) or 0.0)
        end = float(u.get("end", 0.0) or 0.0)
        txt = (u.get("text") or "").strip()
        if not txt:
            continue
        display_override = (u.get("speaker_display_override") or "")
        if isinstance(display_override, str):
            display_override = display_override.strip()
        else:
            display_override = ""

        speaker_name = (display_override or (effective.get(raw) or raw or "Unknown")).strip()
        if speaker_name == "Unknown":
            # Normalize legacy Unknown to Unknown Speaker 1
            speaker_name = "Unknown Speaker 1"
        rows.append({
            "start": start,
            "end": end,
            "speaker_name": speaker_name,
            "text": txt,
            "diarization_speaker": raw or f"SPEAKER_{len(rows)}",
            "is_unknown": speaker_name.startswith("Unknown Speaker"),
        })

    rows = _merge_consecutive_script_rows(rows)
    return {
        "utterances_path": utterances_path,
        "named_txt_path": OUTPUT_DIR / f"{meeting_id}_named_script.txt",
        "named_json_path": OUTPUT_DIR / f"{meeting_id}_named_script.json",
        "transcript_pdf_path": OUTPUT_DIR / f"{meeting_id}_transcript.pdf",
        "meeting_report_pdf_path": OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf",
        "rows": rows,
        "speakers_in_order": speakers_in_order,
        "unknown_by_raw": unknown_by_raw,
    }


def _effective_raw_display_map(meeting_id: str, meeting: dict, utterances: list[dict]) -> dict[str, str]:
    """Compute effective raw diarization label -> display name map for UI rendering."""
    unknown_by_raw, raw_by_unknown, _ = _unknown_map_from_utterances(utterances)
    seeded = _seed_label_map_from_named_json(meeting_id)
    stored_map: dict[str, str] = meeting.get("speaker_label_map", {}) if isinstance(meeting.get("speaker_label_map"), dict) else {}
    stored_map_raw: dict[str, str] = {}
    for k, v in stored_map.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        key = k.strip()
        val = v.strip()
        if not val:
            continue
        if key in unknown_by_raw:
            stored_map_raw[key] = val
        elif key in raw_by_unknown:
            stored_map_raw[raw_by_unknown[key]] = val

    effective_map: dict[str, str] = dict(unknown_by_raw)
    effective_map.update(seeded)
    effective_map.update(stored_map_raw)
    return effective_map


def _regenerate_meeting_assets(meeting_id: str, meeting: dict, skip_ai_summary: bool = True) -> dict[str, Any]:
    """Regenerate named transcript + transcript PDF from the current meeting state.
    
    By default, skips the AI-powered meeting report PDF generation (Ollama) to avoid
    freezing the system. Set skip_ai_summary=False to include it (resource-intensive).
    """
    assets = _build_labeled_script_from_utterances(meeting_id, meeting, {})
    _write_named_script_assets(assets["named_txt_path"], assets["named_json_path"], assets["rows"])
    transcript_pdf = _regenerate_transcript_pdf_from_named_json(meeting_id, assets["named_json_path"])
    
    # Skip Ollama-based meeting report by default - it's very resource intensive
    meeting_report_pdf = None
    if not skip_ai_summary:
        meeting_report_pdf = _regenerate_meeting_report_pdf_from_transcript(meeting_id, meeting, assets["named_txt_path"])
    
    return {
        "assets": assets,
        "transcript_pdf": transcript_pdf,
        "meeting_report_pdf": meeting_report_pdf,
    }


def _write_named_script_assets(named_txt_path: Path, named_json_path: Path, rows: list[dict]) -> None:
    named_txt_path.parent.mkdir(parents=True, exist_ok=True)
    named_json_path.parent.mkdir(parents=True, exist_ok=True)
    named_json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"{r.get('speaker_name', 'Unknown')}: {r.get('text', '')}".strip() for r in rows]
    named_txt_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def _regenerate_transcript_pdf_from_named_json(meeting_id: str, named_json_path: Path) -> Optional[Path]:
    try:
        from email_named_script import create_pdf as _create_pdf, read_db as _read_db
        people = {}
        try:
            # emails.csv may not exist in all deployments; PDF generation still works without it
            people = _read_db(Path("input") / "emails.csv")
        except Exception:
            people = {}
        out_pdf = OUTPUT_DIR / f"{meeting_id}_transcript.pdf"
        ok = _create_pdf(named_json_path, people, out_pdf)
        if ok and out_pdf.exists() and out_pdf.stat().st_size > 0:
            return out_pdf
    except Exception as e:
        print(f"[TRANSCRIPT PDF] Could not generate transcript PDF: {e}")
    return None


def _regenerate_meeting_report_pdf_from_transcript(meeting_id: str, meeting: dict, transcript_path: Path) -> Optional[Path]:
    """Regenerate the meeting report PDF (summary) using meeting_pdf_summarizer/main.py."""
    summarizer_main = ROOT / "meeting_pdf_summarizer" / "main.py"
    roles_json = ROOT / "meeting_pdf_summarizer" / "roles.json"
    if not summarizer_main.exists():
        return None
    try:
        upload_date = meeting.get("processed_at") or datetime.now().isoformat()
        source_orgs = meeting.get("source_organizations", [])
        source_orgs_str = ",".join(source_orgs) if source_orgs else ""

        out_pdf = OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf"
        PY = sys.executable
        cmd = [PY, str(summarizer_main),
               "--input", str(transcript_path),
               "--output", str(out_pdf),
               "--roles", str(roles_json)]
        if upload_date:
            cmd.extend(["--upload-date", upload_date])
        if source_orgs_str:
            cmd.extend(["--source-organizations", source_orgs_str])

        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print(f"[MEETING REPORT] PDF generation failed: {result.stderr}")
            return None
        if out_pdf.exists() and out_pdf.stat().st_size > 0:
            return out_pdf
    except Exception as e:
        print(f"[MEETING REPORT] Could not regenerate meeting report PDF: {e}")
    return None


def _resolve_username_for_label(label_name: str) -> Optional[str]:
    """Best-effort: map a display name/username string to a known username in users.csv."""
    name = (label_name or "").strip()
    if not name:
        return None
    try:
        users = read_users()
        # Direct username match
        for _, u in users.items():
            username = (u.get("username") or "").strip()
            if username and username.lower() == name.lower():
                return username
        # Full name match
        for _, u in users.items():
            username = (u.get("username") or "").strip()
            first = (u.get("first") or "").strip()
            last = (u.get("last") or "").strip()
            if username and first and last:
                if f"{first} {last}".strip().lower() == name.lower():
                    return username
    except Exception:
        return None
    return None


def _ensure_meeting_wav_16k(meeting_id: str, meeting: dict) -> Optional[Path]:
    """Ensure output/<meeting_id>_16k.wav exists; attempt to create from meeting audio if missing."""
    out_wav = OUTPUT_DIR / f"{meeting_id}_16k.wav"
    if out_wav.exists() and out_wav.stat().st_size > 0:
        return out_wav

    # Resolve audio source
    audio_src = None
    if meeting.get("audio_path"):
        candidate = ROOT / str(meeting["audio_path"])
        if candidate.exists():
            audio_src = candidate
    if audio_src is None:
        # Fallback to input directory common extensions
        for ext in [".webm", ".m4a", ".mp4", ".wav", ".mp3", ".mov", ".aac", ".flac", ".ogg"]:
            c = INPUT_DIR / f"{meeting_id}{ext}"
            if c.exists():
                audio_src = c
                break
    if audio_src is None:
        return None

    try:
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_src),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(out_wav),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[LEARN] ffmpeg convert failed: {result.stderr}")
            return None
        if out_wav.exists() and out_wav.stat().st_size > 0:
            return out_wav
    except Exception as e:
        print(f"[LEARN] Could not create 16k wav: {e}")
    return None


def _get_final_speaker_display(u: dict, raw_label_map: dict[str, str]) -> str:
    """
    Get the final display name for an utterance, applying:
    1. Per-utterance override (speaker_display_override) if set
    2. Bulk label map (raw -> display name) otherwise
    3. Fall back to raw label or "Unknown"
    """
    # Check for per-utterance override first
    display_override = (u.get("speaker_display_override") or "")
    if isinstance(display_override, str):
        display_override = display_override.strip()
    else:
        display_override = ""
    
    if display_override:
        return display_override
    
    # Otherwise use bulk label map
    raw = (u.get("speaker") or "").strip()
    return (raw_label_map.get(raw) or raw or "Unknown").strip()


def _try_learn_enrollment_from_meeting(meeting_id: str, meeting: dict, speaker_display_name: str) -> bool:
    """
    Best-effort "global learning": extract audio for a speaker based on their FINAL DISPLAY NAME
    (after all user corrections, splits, and overrides are applied).
    
    This uses effective_utterances which includes user corrections, so when users fix
    diarization mistakes in the UI, those corrected segments are used for enrollment.
    """
    enrollment_key = ensure_speaker_profile(speaker_display_name)
    if not enrollment_key:
        print(f"[ENROLL] ❌ Could not create enrollment key for {speaker_display_name}", flush=True)
        return False
    _append_speaker_profile_evidence(enrollment_key, meeting_id, speaker_display_name)

    # Get effective utterances (includes user splits and overrides)
    effective_utterances = _effective_utterances_for_meeting(meeting_id, meeting)
    if not effective_utterances:
        print(f"[ENROLL] ❌ No effective utterances found for {meeting_id}", flush=True)
        return False

    # Build raw -> display label map for determining final speaker names
    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    if not utterances_path.exists():
        return False
    try:
        src_utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    unknown_by_raw, _, _ = _unknown_map_from_utterances(src_utterances)
    
    # Build effective raw -> display map
    stored_map: dict[str, str] = meeting.get("speaker_label_map", {}) if isinstance(meeting.get("speaker_label_map"), dict) else {}
    raw_label_map: dict[str, str] = dict(unknown_by_raw)
    for k, v in stored_map.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            raw_label_map[k.strip()] = v.strip()

    wav16k = _ensure_meeting_wav_16k(meeting_id, meeting)
    if not wav16k:
        print(f"[ENROLL] ❌ Could not get/create 16k wav for {meeting_id}", flush=True)
        return False

    # Collect segments where the FINAL display name matches (not raw diarization label)
    # This respects user corrections made via "New Speaker" button
    segments = []
    total = 0.0
    skipped_overlap = 0
    for u in effective_utterances:
        # Get the final speaker display name after all corrections
        final_name = _get_final_speaker_display(u, raw_label_map)
        
        if final_name != speaker_display_name:
            continue
        
        # Skip segments flagged as having speaker overlap (contaminated audio)
        if u.get("needs_review") or u.get("speaker_overlap"):
            skipped_overlap += 1
            continue
            
        start = float(u.get("start", 0.0) or 0.0)
        end = float(u.get("end", 0.0) or 0.0)
        dur = max(0.0, end - start)
        if dur < 0.6:
            continue
        segments.append((start, end))
        total += dur
        if total >= 17.0:
            break

    if skipped_overlap > 0:
        print(f"[ENROLL] ℹ️ Skipped {skipped_overlap} segments with speaker overlap for {speaker_display_name}", flush=True)

    if total < 15.0:
        # Enrollment requires >= 15s of clean audio to be useful
        print(f"[ENROLL] ⏭️ Not enough clean audio for {speaker_display_name} ({total:.1f}s < 15s)", flush=True)
        return False

    # Use a safe directory name (replace spaces and special chars)
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in speaker_display_name)
    tmp_dir = OUTPUT_DIR / "_learn_segs" / meeting_id / safe_name
    tmp_dir.mkdir(parents=True, exist_ok=True)
    seg_files = []
    try:
        print(f"[ENROLL] Extracting {len(segments)} segments ({total:.1f}s) for {speaker_display_name}...", flush=True)
        for i, (start, end) in enumerate(segments):
            seg_path = tmp_dir / f"seg_{i:03d}.wav"
            dur = max(0.01, end - start)
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-t", f"{dur:.3f}",
                "-i", str(wav16k),
                "-ac", "1",
                "-ar", "16000",
                "-c:a", "pcm_s16le",
                str(seg_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"[ENROLL] ❌ ffmpeg extract failed for segment {i}", flush=True)
                return False
            seg_files.append(seg_path)

        # Concat segments into one wav
        list_file = tmp_dir / "concat.txt"
        list_file.write_text("\n".join([f"file '{p.as_posix()}'" for p in seg_files]) + "\n", encoding="utf-8")
        concat_path = tmp_dir / "concat.wav"
        cmd_concat = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(concat_path),
        ]
        result = subprocess.run(cmd_concat, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[ENROLL] ❌ ffmpeg concat failed", flush=True)
            return False
        if not concat_path.exists() or concat_path.stat().st_size == 0:
            print(f"[ENROLL] ❌ Concat file empty or missing", flush=True)
            return False

        # Pick an available enrollment filename: key.wav, key(2).wav, ...
        ENROLL_DIR.mkdir(parents=True, exist_ok=True)
        n = 1
        while True:
            name = f"{enrollment_key}.wav" if n == 1 else f"{enrollment_key}({n}).wav"
            dest = ENROLL_DIR / name
            if not dest.exists():
                shutil.copyfile(concat_path, dest)
                print(f"[ENROLL] ✅ Added enrollment sample: {dest.name} ({total:.1f}s from {meeting_id})", flush=True)
                return True
            n += 1
            if n > 25:
                print(f"[ENROLL] ❌ Too many enrollment files for {enrollment_key}", flush=True)
                return False
    except Exception as e:
        print(f"[ENROLL] ❌ Failed to learn enrollment sample: {e}", flush=True)
        return False
    finally:
        # Clean up temp directory best-effort
        try:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@app.post("/api/meetings/<meeting_id>/speaker_labels")
def api_save_speaker_labels(meeting_id: str):
    """Save diarization-speaker label mapping (raw diarization label -> display name) and regenerate assets."""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    user = current_user()
    editor_email = (user.get("email") if user else None) or session.get("user_email") or "unknown"

    meeting = get_meeting(meeting_id)
    if not meeting:
        return jsonify({"error": "Meeting not found"}), 404

    data = request.get_json() or {}
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        return jsonify({"error": "Labels must be a dictionary"}), 400

    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    if not utterances_path.exists():
        return jsonify({"error": "Utterances not found"}), 404
    try:
        utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "Invalid utterances.json"}), 500
    if not isinstance(utterances, list):
        return jsonify({"error": "utterances.json must be a list"}), 500

    unknown_by_raw, raw_by_unknown, speakers_in_order = _unknown_map_from_utterances(utterances)
    known_raw = set(speakers_in_order)

    raw_overrides: dict[str, str] = {}
    for k, v in labels.items():
        if not isinstance(k, str):
            continue
        name = (v or "")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue  # empty means "clear" (handled client-side by omitting)
        key = k.strip()
        # Accept raw diarization labels and Unknown Speaker N keys
        if key in known_raw:
            raw_overrides[key] = name
        elif key in raw_by_unknown:
            raw_overrides[raw_by_unknown[key]] = name

    if not raw_overrides:
        return jsonify({"error": "No valid labels provided"}), 400

    # Persist mapping (raw diarization label -> display name) + history
    existing_map = meeting.get("speaker_label_map", {}) if isinstance(meeting.get("speaker_label_map"), dict) else {}
    # Normalize: keep only string->string
    normalized_existing: dict[str, str] = {}
    for kk, vv in existing_map.items():
        if isinstance(kk, str) and isinstance(vv, str) and vv.strip():
            normalized_existing[kk.strip()] = vv.strip()

    normalized_existing.update(raw_overrides)

    labels_version = int(meeting.get("labels_version", 0) or 0) + 1
    history = meeting.get("speaker_label_map_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "version": labels_version,
        "updated_at": datetime.now().isoformat(),
        "updated_by": str(editor_email),
        "labels": dict(raw_overrides),
    })

    # Regenerate assets (named_script txt/json + transcript pdf only - skip AI summary to avoid freezing)
    try:
        assets = _build_labeled_script_from_utterances(meeting_id, meeting, normalized_existing)
        _write_named_script_assets(assets["named_txt_path"], assets["named_json_path"], assets["rows"])
        transcript_pdf = _regenerate_transcript_pdf_from_named_json(meeting_id, assets["named_json_path"])
        # Skip Ollama-based meeting report regeneration - user can trigger via "Generate AI Summary" button
        meeting_report_pdf = None
    except Exception as e:
        return jsonify({"error": "Regeneration failed", "message": str(e)}), 500

    # Best-effort: "global learning" by adding enrollment samples
    # Run in background threads so the UI doesn't slow down
    # IMPORTANT: Use the updated meeting data with new speaker_label_map so enrollment
    # can correctly compute final display names including user corrections
    meeting_for_enrollment = dict(meeting)
    meeting_for_enrollment["speaker_label_map"] = normalized_existing
    
    def _enroll_speaker_background(m_id, m_data, speaker_name):
        try:
            result = _try_learn_enrollment_from_meeting(m_id, m_data, speaker_name)
            # Result logging is now handled inside the function
        except Exception as e:
            print(f"[ENROLL] ❌ Failed to enroll {speaker_name}: {e}", flush=True)

    # Get unique speaker display names to enroll (avoid duplicate enrollments)
    speaker_names_to_enroll = set(raw_overrides.values())
    learned = {}
    for name in speaker_names_to_enroll:
        print(f"[ENROLL] Starting background enrollment for {name}...", flush=True)
        thread = threading.Thread(
            target=_enroll_speaker_background,
            args=(meeting_id, meeting_for_enrollment, name),
            daemon=True
        )
        thread.start()
        learned[name] = "pending"  # Enrollment is happening in background

    update_meeting(meeting_id, {
        "speaker_label_map": normalized_existing,
        "speaker_label_map_history": history,
        "labels_updated_at": datetime.now().isoformat(),
        "labels_version": labels_version,
        "transcript_path": str((OUTPUT_DIR / f"{meeting_id}_named_script.txt").relative_to(ROOT)),
        "transcript_updated_at": datetime.now().isoformat(),
        "transcript_pdf_path": str((OUTPUT_DIR / f"{meeting_id}_transcript.pdf").relative_to(ROOT)) if transcript_pdf else meeting.get("transcript_pdf_path"),
        # Don't overwrite pdf_path - AI summary is generated on-demand via separate button
    })

    meeting = get_meeting(meeting_id) or meeting
    return jsonify({
        "status": "success",
        "meeting": meeting,
        "labels_version": labels_version,
        "regenerated": {
            "named_script_txt": str(OUTPUT_DIR / f"{meeting_id}_named_script.txt"),
            "named_script_json": str(OUTPUT_DIR / f"{meeting_id}_named_script.json"),
            "transcript_pdf": bool(transcript_pdf),
            "meeting_report_pdf": bool(meeting_report_pdf),
            "learned_enrollment": learned,
        }
    }), 200


@app.post("/api/meetings/<meeting_id>/utterance_overrides")
def api_save_utterance_override(meeting_id: str):
    """Save a per-utterance speaker display override (single instance) and regenerate assets."""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    user = current_user()
    editor_email = (user.get("email") if user else None) or session.get("user_email") or "unknown"

    meeting = get_meeting(meeting_id)
    if not meeting:
        return jsonify({"error": "Meeting not found"}), 404

    data = request.get_json() or {}
    utterance_id = (data.get("utterance_id") or "").strip()
    speaker_display = data.get("speaker_display")
    if not utterance_id:
        return jsonify({"error": "utterance_id required"}), 400
    if speaker_display is None or not isinstance(speaker_display, str):
        return jsonify({"error": "speaker_display must be a string (can be empty to clear)"}), 400
    speaker_display = speaker_display.strip()

    overrides = meeting.get("utterance_overrides", [])
    if not isinstance(overrides, list):
        overrides = []

    existing_created_at = None
    for it in overrides:
        if isinstance(it, dict) and (it.get("type") or "").strip() == "speaker_display_override" and (it.get("utterance_id") or "").strip() == utterance_id:
            existing_created_at = it.get("created_at")
            break

    # Remove existing override for this utterance
    overrides = [
        it for it in overrides
        if not (isinstance(it, dict) and (it.get("type") or "").strip() == "speaker_display_override" and (it.get("utterance_id") or "").strip() == utterance_id)
    ]

    now = datetime.now().isoformat()
    if speaker_display:
        overrides.append({
            "utterance_id": utterance_id,
            "type": "speaker_display_override",
            "speaker_display": speaker_display,
            "created_at": existing_created_at or now,
            "updated_at": now,
            "updated_by": str(editor_email),
        })

    version = int(meeting.get("utterance_overrides_version", 0) or 0) + 1
    history = meeting.get("utterance_overrides_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "version": version,
        "updated_at": now,
        "updated_by": str(editor_email),
        "override": {"utterance_id": utterance_id, "speaker_display": speaker_display},
    })

    update_meeting(meeting_id, {
        "utterance_overrides": overrides,
        "utterance_overrides_version": version,
        "utterance_overrides_updated_at": now,
        "utterance_overrides_history": history,
    })

    meeting = get_meeting(meeting_id) or meeting

    # Regenerate assets
    try:
        regen = _regenerate_meeting_assets(meeting_id, meeting)
        assets = regen["assets"]
        transcript_pdf = regen["transcript_pdf"]
        meeting_report_pdf = regen["meeting_report_pdf"]
    except Exception as e:
        return jsonify({"error": "Regeneration failed", "message": str(e)}), 500

    update_meeting(meeting_id, {
        "transcript_path": str((OUTPUT_DIR / f"{meeting_id}_named_script.txt").relative_to(ROOT)),
        "transcript_updated_at": datetime.now().isoformat(),
        "transcript_pdf_path": str((OUTPUT_DIR / f"{meeting_id}_transcript.pdf").relative_to(ROOT)) if transcript_pdf else meeting.get("transcript_pdf_path"),
        "pdf_path": str((OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf").relative_to(ROOT)) if meeting_report_pdf else meeting.get("pdf_path"),
        "pdf_updated_at": datetime.now().isoformat() if meeting_report_pdf else meeting.get("pdf_updated_at"),
    })

    meeting = get_meeting(meeting_id) or meeting

    # Best-effort enrollment for the new speaker (in background so UI doesn't slow down)
    # Only trigger if a speaker name was provided (not clearing)
    if speaker_display:
        def _enroll_single_override_background(m_id, m_data, speaker_name):
            try:
                result = _try_learn_enrollment_from_meeting(m_id, m_data, speaker_name)
                # Result logging is handled inside the function
            except Exception as e:
                print(f"[ENROLL] ❌ Failed to enroll {speaker_name}: {e}", flush=True)
        
        print(f"[ENROLL] Starting background enrollment for {speaker_display} (from utterance override)...", flush=True)
        thread = threading.Thread(
            target=_enroll_single_override_background,
            args=(meeting_id, meeting, speaker_display),
            daemon=True
        )
        thread.start()

    # Return updated utterance payload for fast UI update
    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    try:
        base_utterances = json.loads(utterances_path.read_text(encoding="utf-8")) if utterances_path.exists() else []
    except Exception:
        base_utterances = []
    effective_map = _effective_raw_display_map(meeting_id, meeting, base_utterances if isinstance(base_utterances, list) else [])
    effective_utterances = _effective_utterances_for_meeting(meeting_id, meeting)
    updated = None
    for u in effective_utterances:
        if (u.get("utterance_id") or "") == utterance_id:
            raw = (u.get("speaker") or "").strip()
            display_override = u.get("speaker_display_override")
            if isinstance(display_override, str):
                display_override = display_override.strip()
            else:
                display_override = ""
            updated = {
                "utterance_id": utterance_id,
                "source_utterance_id": (u.get("source_utterance_id") or ""),
                "start": float(u.get("start", 0.0) or 0.0),
                "end": float(u.get("end", 0.0) or 0.0),
                "speaker_raw": raw,
                "speaker_display": (display_override or effective_map.get(raw, raw)),
                "speaker_confidence_percent": int(u.get("speaker_confidence_percent") or 0),
                "text": (u.get("text") or ""),
            }
            break

    return jsonify({
        "status": "success",
        "updated_utterance": updated,
        "regenerated": {
            "named_script_txt": str(assets["named_txt_path"]),
            "named_script_json": str(assets["named_json_path"]),
            "transcript_pdf": bool(transcript_pdf),
            "meeting_report_pdf": bool(meeting_report_pdf),
        },
        "utterance_overrides_version": version,
    }), 200


@app.post("/api/meetings/<meeting_id>/utterance_split")
def api_split_utterance(meeting_id: str):
    """Split an utterance at a word index and optionally set per-part speaker display, then regenerate assets. Supports multiple splits."""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    user = current_user()
    editor_email = (user.get("email") if user else None) or session.get("user_email") or "unknown"

    meeting = get_meeting(meeting_id)
    if not meeting:
        return jsonify({"error": "Meeting not found"}), 404

    data = request.get_json() or {}
    source_utterance_id = (data.get("utterance_id") or "").strip()
    if not source_utterance_id:
        return jsonify({"error": "utterance_id required"}), 400

    # Accept either split_word_index (preferred) or split_time (backward compatibility)
    split_word_index = data.get("split_word_index")
    split_time = data.get("split_time")
    
    if split_word_index is None and split_time is None:
        return jsonify({"error": "split_word_index or split_time required"}), 400

    speaker_a = data.get("speaker_display_a")
    speaker_b = data.get("speaker_display_b")
    if speaker_a is not None and not isinstance(speaker_a, str):
        return jsonify({"error": "speaker_display_a must be a string"}), 400
    if speaker_b is not None and not isinstance(speaker_b, str):
        return jsonify({"error": "speaker_display_b must be a string"}), 400
    speaker_a = (speaker_a or "").strip() if isinstance(speaker_a, str) else ""
    speaker_b = (speaker_b or "").strip() if isinstance(speaker_b, str) else ""

    # Load base utterances to validate
    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    if not utterances_path.exists():
        return jsonify({"error": "Utterances not found"}), 404
    try:
        base_utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "Invalid utterances.json"}), 500
    if not isinstance(base_utterances, list):
        return jsonify({"error": "utterances.json must be a list"}), 500

    matched = None
    for u in base_utterances:
        if not isinstance(u, dict):
            continue
        if _utterance_id_for_item(u) == source_utterance_id:
            matched = u
            break
    if not matched:
        return jsonify({"error": "Utterance not found"}), 404

    start = float(matched.get("start", 0.0) or 0.0)
    end = float(matched.get("end", 0.0) or 0.0)
    txt = (matched.get("text") or "").strip()
    words = matched.get("words")
    txt_words = txt.split()
    
    # Calculate split_word_index and split_time
    if split_word_index is not None:
        try:
            split_word_index = int(split_word_index)
            # Validate word index (0-based: split before this word, so valid range is 1 to len(words)-1)
            word_count = len(txt_words) if txt_words else (len(words) if isinstance(words, list) else 1)
            if split_word_index < 1 or split_word_index >= word_count:
                return jsonify({"error": f"split_word_index must be between 1 and {word_count - 1} (split before word {split_word_index + 1})"}), 400
            
            # Calculate split_time from word timestamp if available
            if isinstance(words, list) and split_word_index < len(words):
                split_time = float(words[split_word_index].get("start", 0.0))
            else:
                # Proportional calculation
                dur = end - start
                if word_count > 0:
                    split_time = start + (split_word_index / word_count) * dur
                else:
                    split_time = (start + end) / 2.0
        except (ValueError, TypeError):
            return jsonify({"error": "split_word_index must be an integer"}), 400
    else:
        # Backward compatibility: calculate word_index from split_time
        try:
            split_time = float(split_time)
            if not (start < split_time < end):
                return jsonify({"error": "split_time must be within (start, end)"}), 400
            split_word_index = _choose_split_word_index(txt, start, end, split_time)
        except (ValueError, TypeError):
            return jsonify({"error": "split_time must be a number"}), 400

    # Get or create split object for this utterance
    splits = meeting.get("utterance_splits", [])
    if not isinstance(splits, list):
        splits = []
    
    # Find existing split object or create new one
    existing_split_obj = None
    for it in splits:
        if isinstance(it, dict) and (it.get("utterance_id") or "").strip() == source_utterance_id:
            existing_split_obj = it
            break
    
    now = datetime.now().isoformat()
    
    if existing_split_obj:
        # Add to existing splits array
        splits_array = existing_split_obj.get("splits", [])
        if not isinstance(splits_array, list):
            splits_array = []
        
        # Validate new split is in a valid position
        existing_word_indices = sorted([int(s.get("word_index", 0)) for s in splits_array])
        
        # Check for duplicate
        if split_word_index in existing_word_indices:
            return jsonify({"error": f"Split already exists at word {split_word_index + 1}"}), 400
        
        if existing_word_indices:
            # Check if new split is between any two existing splits or at valid boundaries
            valid = False
            if split_word_index > 0 and split_word_index < len(txt_words):
                # Check if it's between any existing splits
                for i in range(len(existing_word_indices) - 1):
                    if existing_word_indices[i] < split_word_index < existing_word_indices[i + 1]:
                        valid = True
                        break
                # Or if it's before the first split
                if split_word_index < existing_word_indices[0]:
                    valid = True
                # Or if it's after the last split
                if split_word_index > existing_word_indices[-1]:
                    valid = True
            if not valid:
                return jsonify({"error": f"New split must be at a valid position (not at word 0 or {len(txt_words)}, and not duplicate)"}), 400
        else:
            # First split - just validate it's not at boundaries
            if split_word_index <= 0 or split_word_index >= len(txt_words):
                return jsonify({"error": f"Split must be between word 1 and {len(txt_words) - 1}"}), 400
        
        # Add new split
        splits_array.append({
            "word_index": int(split_word_index),
            "split_time": float(split_time),
            "speaker_display": speaker_b or None,
        })
        
        # Sort by word_index
        splits_array = sorted(splits_array, key=lambda x: int(x.get("word_index", 0)))
        
        # Update existing split object
        existing_split_obj["splits"] = splits_array
        existing_split_obj["updated_at"] = now
        existing_split_obj["updated_by"] = str(editor_email)
        
        # Update part_a for first part if this is the first split
        if len(splits_array) == 1 and speaker_a:
            if "part_a" not in existing_split_obj:
                existing_split_obj["part_a"] = {}
            existing_split_obj["part_a"]["speaker_display"] = speaker_a
    else:
        # Create new split object
        new_split_obj = {
            "utterance_id": source_utterance_id,
            "splits": [{
                "word_index": int(split_word_index),
                "split_time": float(split_time),
                "speaker_display": speaker_b or None,
            }],
            "part_a": {"speaker_display": speaker_a or None},
            "created_at": now,
            "updated_at": now,
            "updated_by": str(editor_email),
        }
        splits.append(new_split_obj)

    version = int(meeting.get("utterance_splits_version", 0) or 0) + 1
    history = meeting.get("utterance_splits_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "version": version,
        "updated_at": now,
        "updated_by": str(editor_email),
        "split": {"utterance_id": source_utterance_id, "split_time": float(split_time)},
    })

    update_meeting(meeting_id, {
        "utterance_splits": splits,
        "utterance_splits_version": version,
        "utterance_splits_updated_at": now,
        "utterance_splits_history": history,
    })

    meeting = get_meeting(meeting_id) or meeting

    # Regenerate assets
    try:
        regen = _regenerate_meeting_assets(meeting_id, meeting)
        assets = regen["assets"]
        transcript_pdf = regen["transcript_pdf"]
        meeting_report_pdf = regen["meeting_report_pdf"]
    except Exception as e:
        return jsonify({"error": "Regeneration failed", "message": str(e)}), 500

    update_meeting(meeting_id, {
        "transcript_path": str((OUTPUT_DIR / f"{meeting_id}_named_script.txt").relative_to(ROOT)),
        "transcript_updated_at": datetime.now().isoformat(),
        "transcript_pdf_path": str((OUTPUT_DIR / f"{meeting_id}_transcript.pdf").relative_to(ROOT)) if transcript_pdf else meeting.get("transcript_pdf_path"),
        "pdf_path": str((OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf").relative_to(ROOT)) if meeting_report_pdf else meeting.get("pdf_path"),
        "pdf_updated_at": datetime.now().isoformat() if meeting_report_pdf else meeting.get("pdf_updated_at"),
    })

    meeting = get_meeting(meeting_id) or meeting

    effective_map = _effective_raw_display_map(meeting_id, meeting, base_utterances)
    effective_utterances = _effective_utterances_for_meeting(meeting_id, meeting)
    new_utterances = []
    for u in effective_utterances:
        if (u.get("source_utterance_id") or "") == source_utterance_id:
            raw = (u.get("speaker") or "").strip()
            display_override = u.get("speaker_display_override")
            if isinstance(display_override, str):
                display_override = display_override.strip()
            else:
                display_override = ""
            utterance_dict = {
                "utterance_id": (u.get("utterance_id") or ""),
                "source_utterance_id": source_utterance_id,
                "start": float(u.get("start", 0.0) or 0.0),
                "end": float(u.get("end", 0.0) or 0.0),
                "speaker_raw": raw,
                "speaker_display": (display_override or effective_map.get(raw, raw)),
                "speaker_confidence_percent": int(u.get("speaker_confidence_percent") or 0),
                "text": (u.get("text") or ""),
            }
            
            # Include word-level data if available
            if "words" in u and isinstance(u["words"], list):
                utterance_dict["words"] = u["words"]
            
            new_utterances.append(utterance_dict)

    return jsonify({
        "status": "success",
        "new_utterances": new_utterances,
        "regenerated": {
            "named_script_txt": str(assets["named_txt_path"]),
            "named_script_json": str(assets["named_json_path"]),
            "transcript_pdf": bool(transcript_pdf),
            "meeting_report_pdf": bool(meeting_report_pdf),
        },
        "utterance_splits_version": version,
    }), 200

@app.post("/api/meetings/<meeting_id>/utterance_split/undo")
def api_undo_utterance_split(meeting_id: str):
    """Undo a split by removing it from utterance_splits and regenerating assets."""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    user = current_user()
    editor_email = (user.get("email") if user else None) or session.get("user_email") or "unknown"

    meeting = get_meeting(meeting_id)
    if not meeting:
        return jsonify({"error": "Meeting not found"}), 404

    data = request.get_json() or {}
    source_utterance_id = (data.get("source_utterance_id") or "").strip()
    if not source_utterance_id:
        return jsonify({"error": "source_utterance_id required"}), 400
    
    split_index = data.get("split_index")  # Optional: specific split to remove

    splits = meeting.get("utterance_splits", [])
    if not isinstance(splits, list):
        splits = []

    # Find the split object
    split_obj = None
    for it in splits:
        if isinstance(it, dict) and (it.get("utterance_id") or "").strip() == source_utterance_id:
            split_obj = it
            break
    
    if not split_obj:
        return jsonify({"error": "Split not found"}), 404
    
    # Handle removing specific split or all splits
    if split_index is not None:
        # Remove specific split from array
        try:
            split_index = int(split_index)
            splits_array = split_obj.get("splits", [])
            if not isinstance(splits_array, list) or split_index < 0 or split_index >= len(splits_array):
                return jsonify({"error": "Invalid split_index"}), 400
            
            splits_array.pop(split_index)
            
            if len(splits_array) == 0:
                # No splits left, remove entire split object
                splits = [it for it in splits if it != split_obj]
            else:
                # Update splits array
                split_obj["splits"] = splits_array
                split_obj["updated_at"] = datetime.now().isoformat()
                split_obj["updated_by"] = str(editor_email)
        except (ValueError, TypeError):
            return jsonify({"error": "split_index must be an integer"}), 400
    else:
        # Remove all splits (remove entire split object)
        splits = [it for it in splits if it != split_obj]

    now = datetime.now().isoformat()
    version = int(meeting.get("utterance_splits_version", 0) or 0) + 1
    history = meeting.get("utterance_splits_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "version": version,
        "updated_at": now,
        "updated_by": str(editor_email),
        "action": "undo_split",
        "utterance_id": source_utterance_id,
    })

    update_meeting(meeting_id, {
        "utterance_splits": splits,
        "utterance_splits_version": version,
        "utterance_splits_updated_at": now,
        "utterance_splits_history": history,
    })

    meeting = get_meeting(meeting_id) or meeting

    # Regenerate assets
    try:
        regen = _regenerate_meeting_assets(meeting_id, meeting)
        assets = regen["assets"]
        transcript_pdf = regen["transcript_pdf"]
        meeting_report_pdf = regen["meeting_report_pdf"]
    except Exception as e:
        return jsonify({"error": "Regeneration failed", "message": str(e)}), 500

    update_meeting(meeting_id, {
        "transcript_path": str((OUTPUT_DIR / f"{meeting_id}_named_script.txt").relative_to(ROOT)),
        "transcript_updated_at": datetime.now().isoformat(),
        "transcript_pdf_path": str((OUTPUT_DIR / f"{meeting_id}_transcript.pdf").relative_to(ROOT)) if transcript_pdf else meeting.get("transcript_pdf_path"),
        "pdf_path": str((OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf").relative_to(ROOT)) if meeting_report_pdf else meeting.get("pdf_path"),
        "pdf_updated_at": datetime.now().isoformat() if meeting_report_pdf else meeting.get("pdf_updated_at"),
    })

    # Get the restored utterance
    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    if not utterances_path.exists():
        return jsonify({"error": "Utterances not found"}), 404
    
    try:
        base_utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "Invalid utterances.json"}), 500
    
    if not isinstance(base_utterances, list):
        return jsonify({"error": "Invalid utterances format"}), 500

    # Build effective map for display
    effective_map = _effective_raw_display_map(meeting_id, meeting, base_utterances)
    effective_utterances = _effective_utterances_for_meeting(meeting_id, meeting)
    
    # Find the restored utterance in effective list
    restored_utterance = None
    for u in effective_utterances:
        if (u.get("utterance_id") or "") == source_utterance_id:
            raw = (u.get("speaker") or "").strip()
            display_override = u.get("speaker_display_override")
            if isinstance(display_override, str):
                display_override = display_override.strip()
            else:
                display_override = ""
            restored_utterance = {
                "utterance_id": source_utterance_id,
                "source_utterance_id": source_utterance_id,
                "start": float(u.get("start", 0.0) or 0.0),
                "end": float(u.get("end", 0.0) or 0.0),
                "speaker_raw": raw,
                "speaker_display": (display_override or effective_map.get(raw, raw)),
                "speaker_confidence_percent": int(u.get("speaker_confidence_percent") or 0),
                "text": (u.get("text") or ""),
            }
            # Include words if available
            if "words" in u and isinstance(u["words"], list):
                restored_utterance["words"] = u["words"]
            break
    
    # If no restored utterance found but splits were removed, return all parts that remain
    if not restored_utterance:
        # Get all remaining parts
        remaining_parts = []
        for u in effective_utterances:
            if (u.get("source_utterance_id") or "") == source_utterance_id:
                raw = (u.get("speaker") or "").strip()
                display_override = u.get("speaker_display_override")
                if isinstance(display_override, str):
                    display_override = display_override.strip()
                else:
                    display_override = ""
                part_dict = {
                    "utterance_id": (u.get("utterance_id") or ""),
                    "source_utterance_id": source_utterance_id,
                    "start": float(u.get("start", 0.0) or 0.0),
                    "end": float(u.get("end", 0.0) or 0.0),
                    "speaker_raw": raw,
                    "speaker_display": (display_override or effective_map.get(raw, raw)),
                    "speaker_confidence_percent": int(u.get("speaker_confidence_percent") or 0),
                    "text": (u.get("text") or ""),
                }
                if "words" in u and isinstance(u["words"], list):
                    part_dict["words"] = u["words"]
                remaining_parts.append(part_dict)
        
        if remaining_parts:
            return jsonify({
                "status": "success",
                "remaining_parts": remaining_parts,
                "regenerated": {
                    "named_script_txt": str(assets["named_txt_path"]),
                    "named_script_json": str(assets["named_json_path"]),
                    "transcript_pdf": bool(transcript_pdf),
                    "meeting_report_pdf": bool(meeting_report_pdf),
                },
                "utterance_splits_version": version,
            }), 200

    if not restored_utterance:
        return jsonify({"error": "Restored utterance not found"}), 404

    return jsonify({
        "status": "success",
        "restored_utterance": restored_utterance,
        "regenerated": {
            "named_script_txt": str(assets["named_txt_path"]),
            "named_script_json": str(assets["named_json_path"]),
            "transcript_pdf": bool(transcript_pdf),
            "meeting_report_pdf": bool(meeting_report_pdf),
        },
        "utterance_splits_version": version,
    }), 200


@app.post("/api/meetings/<meeting_id>/rerun_diarization")
def api_rerun_diarization(meeting_id: str):
    """
    Re-run diarization/transcription with a specified speaker count.
    This allows users to add or remove speakers and get better diarization results.
    
    Request body:
    {
        "num_speakers": int  # Target number of speakers (>= 1)
        "preserve_names": bool  # Optional, try to preserve speaker name mappings (default: true)
    }
    """
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    meeting = get_meeting(meeting_id)
    if not meeting:
        return jsonify({"error": "Meeting not found"}), 404

    data = request.get_json() or {}
    num_speakers = data.get("num_speakers")
    preserve_names = data.get("preserve_names", True)
    
    if num_speakers is None:
        return jsonify({"error": "num_speakers is required"}), 400
    
    try:
        num_speakers = int(num_speakers)
    except (TypeError, ValueError):
        return jsonify({"error": "num_speakers must be an integer"}), 400
    
    if num_speakers < 1:
        return jsonify({"error": "num_speakers must be at least 1"}), 400
    
    # Find the audio file for this meeting
    audio_path = None
    if meeting.get("audio_path"):
        candidate = ROOT / str(meeting["audio_path"])
        if candidate.exists():
            audio_path = candidate
    
    # Fallback: try to find audio in input directory
    if audio_path is None:
        for ext in [".m4a", ".webm", ".mp4", ".wav", ".mp3", ".mov", ".aac", ".flac", ".ogg"]:
            candidate = INPUT_DIR / f"{meeting_id}{ext}"
            if candidate.exists():
                audio_path = candidate
                break
    
    if audio_path is None or not audio_path.exists():
        return jsonify({"error": "Audio file not found. Cannot re-run diarization."}), 404
    
    print(f"[RERUN DIARIZATION] Starting for {meeting_id} with {num_speakers} speakers...")
    print(f"[RERUN DIARIZATION] Audio path: {audio_path}")
    
    # Preserve existing speaker label map if requested
    existing_label_map = {}
    if preserve_names:
        existing_label_map = meeting.get("speaker_label_map", {}) if isinstance(meeting.get("speaker_label_map"), dict) else {}
        print(f"[RERUN DIARIZATION] Preserving {len(existing_label_map)} speaker name mappings")
    
    # Prepare environment
    PY = sys.executable
    env = os.environ.copy()
    
    # Load .env file
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            with open(env_file, 'r', encoding='utf-8-sig') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        env[key] = value
        except Exception as e:
            print(f"[RERUN DIARIZATION] Warning: Could not load .env file: {e}")
    
    stem = meeting_id
    
    # Step 1: Re-run transcribe.py with new speaker count
    # Use --force-speakers to force exact count (min=max=num) since user manually specified
    cmd1 = [PY, "transcribe.py", str(audio_path), "--speakers", str(num_speakers), "--force-speakers"]
    print(f"[RERUN DIARIZATION] Running: {' '.join(cmd1)}")
    
    try:
        result1 = subprocess.run(
            cmd1,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800  # 30 minute timeout
        )
        if result1.returncode != 0:
            print(f"[RERUN DIARIZATION] transcribe.py failed: {result1.stderr}")
            return jsonify({
                "error": "Transcription failed",
                "details": result1.stderr[-500:] if result1.stderr else "Unknown error"
            }), 500
        print(f"[RERUN DIARIZATION] Transcription complete")
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Transcription timed out (30 minutes)"}), 500
    except Exception as e:
        return jsonify({"error": f"Transcription error: {str(e)}"}), 500
    
    # Step 2: Re-run identify_speakers.py
    cmd2 = [
        PY, "identify_speakers.py",
        str(OUTPUT_DIR / f"{stem}_utterances.json"),
        str(ENROLL_DIR),
        str(OUTPUT_DIR / f"{stem}_named_script.txt")
    ]
    print(f"[RERUN DIARIZATION] Running: {' '.join(cmd2)}")
    
    try:
        result2 = subprocess.run(
            cmd2,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        if result2.returncode != 0:
            print(f"[RERUN DIARIZATION] identify_speakers.py failed: {result2.stderr}")
            # Continue anyway - utterances.json should still be valid
        else:
            print(f"[RERUN DIARIZATION] Speaker identification complete")
    except Exception as e:
        print(f"[RERUN DIARIZATION] Speaker identification error (continuing): {e}")
    
    # Step 3: Clear user overrides/splits (fresh start with new diarization)
    # But preserve speaker_label_map names by trying to map them to new raw labels
    update_data = {
        "utterance_overrides": [],
        "utterance_splits": {},
        "diarization_speaker_count": num_speakers,
        "diarization_rerun_at": datetime.now().isoformat(),
    }
    
    # Load new utterances to get new speaker labels
    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    new_utterances = []
    new_speakers_raw = set()
    if utterances_path.exists():
        try:
            new_utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
            if isinstance(new_utterances, list):
                for u in new_utterances:
                    if isinstance(u, dict):
                        spk = (u.get("speaker") or "").strip()
                        if spk:
                            new_speakers_raw.add(spk)
        except Exception as e:
            print(f"[RERUN DIARIZATION] Error reading new utterances: {e}")
    
    # Try to preserve speaker names by matching to new raw labels (best effort)
    # New speakers are SPEAKER_00, SPEAKER_01, etc. - we can't reliably map old names
    # without voice matching, so we clear the mapping and let user re-assign
    if preserve_names and existing_label_map:
        # We can't reliably map old SPEAKER_XX to new SPEAKER_XX because
        # the diarization may have completely different speaker assignments
        # However, we store the old mapping so the user can see previous names
        update_data["previous_speaker_label_map"] = existing_label_map
        print(f"[RERUN DIARIZATION] Stored previous label map for reference")
    
    # Clear the current label map since raw labels have changed
    update_data["speaker_label_map"] = {}
    
    update_meeting(meeting_id, update_data)
    
    # Step 4: Regenerate transcript PDF
    meeting = get_meeting(meeting_id) or meeting
    try:
        named_json_path = OUTPUT_DIR / f"{meeting_id}_named_script.json"
        if named_json_path.exists():
            _regenerate_transcript_pdf_from_named_json(meeting_id, named_json_path)
            print(f"[RERUN DIARIZATION] Regenerated transcript PDF")
    except Exception as e:
        print(f"[RERUN DIARIZATION] Warning: Could not regenerate PDF: {e}")
    
    # Step 5: Build response with new utterances and speakers
    unknown_by_raw, raw_by_unknown, speakers_in_order = _unknown_map_from_utterances(new_utterances)
    
    # Build speaker list for UI
    speakers = []
    for raw in speakers_in_order:
        display = unknown_by_raw.get(raw, raw)
        speakers.append({
            "raw": raw,
            "display": display,
        })
    
    # Build effective utterances for UI
    effective_map = _effective_raw_display_map(meeting_id, meeting, new_utterances)
    effective_utterances = _effective_utterances_for_meeting(meeting_id, meeting)
    
    utterances_for_ui = []
    for u in effective_utterances:
        raw = (u.get("speaker") or "").strip()
        display_override = u.get("speaker_display_override")
        if isinstance(display_override, str):
            display_override = display_override.strip()
        else:
            display_override = ""
        
        utt_dict = {
            "utterance_id": (u.get("utterance_id") or ""),
            "source_utterance_id": (u.get("source_utterance_id") or ""),
            "start": float(u.get("start", 0.0) or 0.0),
            "end": float(u.get("end", 0.0) or 0.0),
            "speaker_raw": raw,
            "speaker_display": (display_override or effective_map.get(raw, raw)),
            "speaker_confidence_percent": int(u.get("speaker_confidence_percent") or 0),
            "text": (u.get("text") or ""),
        }
        
        if "words" in u and isinstance(u["words"], list):
            utt_dict["words"] = u["words"]
        
        utterances_for_ui.append(utt_dict)
    
    print(f"[RERUN DIARIZATION] Complete! {len(speakers)} speakers, {len(utterances_for_ui)} utterances")
    
    return jsonify({
        "status": "success",
        "speakers": speakers,
        "utterances": utterances_for_ui,
        "num_speakers": num_speakers,
        "previous_label_map": existing_label_map if preserve_names else {},
    }), 200


@app.post("/api/meetings/<meeting_id>/generate_summary")
def api_generate_ai_summary(meeting_id: str):
    """Generate AI-powered meeting summary PDF on demand using Ollama.
    
    This is resource-intensive and may take several minutes. Only call when
    the user explicitly requests an AI summary.
    """
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    meeting = get_meeting(meeting_id)
    if not meeting:
        return jsonify({"error": "Meeting not found"}), 404

    # Check if transcript exists
    transcript_path = OUTPUT_DIR / f"{meeting_id}_named_script.txt"
    if not transcript_path.exists():
        return jsonify({"error": "Transcript not found. Please ensure the meeting has been processed."}), 404

    # Generate the AI summary PDF (this calls Ollama - resource intensive!)
    try:
        print(f"[AI SUMMARY] Starting Ollama-based summary generation for {meeting_id}...")
        meeting_report_pdf = _regenerate_meeting_report_pdf_from_transcript(meeting_id, meeting, transcript_path)
        
        if meeting_report_pdf and meeting_report_pdf.exists():
            # Update meeting record with new PDF path
            update_meeting(meeting_id, {
                "pdf_path": str((OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf").relative_to(ROOT)),
                "pdf_updated_at": datetime.now().isoformat(),
                "ai_summary_generated": True,
                "ai_summary_generated_at": datetime.now().isoformat(),
            })
            
            print(f"[AI SUMMARY] Successfully generated summary for {meeting_id}")
            return jsonify({
                "status": "success",
                "message": "AI summary generated successfully",
                "pdf_path": str(meeting_report_pdf.relative_to(ROOT)),
            }), 200
        else:
            return jsonify({
                "error": "AI summary generation failed",
                "message": "Ollama did not return a valid response. Make sure Ollama is running and the model is available."
            }), 500
            
    except Exception as e:
        print(f"[AI SUMMARY] Error generating summary for {meeting_id}: {e}")
        return jsonify({
            "error": "AI summary generation failed",
            "message": str(e)
        }), 500


def get_user_meetings(user_email: str) -> list:
    """Get all meetings where user is a participant."""
    meetings = load_meetings()
    user_meetings = []
    for meeting in meetings:
        participants = meeting.get("participants", [])
        if user_email.lower() in [p.lower() for p in participants]:
            user_meetings.append(meeting)
    return sorted(user_meetings, key=lambda x: x.get("processed_at", ""), reverse=True)

# ----------------------------
# Chat storage
# ----------------------------
def load_chat_sessions() -> dict:
    """Load chat sessions: {user_email: [sessions]}"""
    if not CHAT_SESSIONS_JSON.exists():
        return {}
    try:
        return json.loads(CHAT_SESSIONS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_chat_sessions(sessions: dict):
    """Save chat sessions"""
    CHAT_SESSIONS_JSON.write_text(json.dumps(sessions, indent=2), encoding="utf-8")

def load_chat_messages() -> dict:
    """Load chat messages: {session_id: [messages]}"""
    if not CHAT_MESSAGES_JSON.exists():
        return {}
    try:
        return json.loads(CHAT_MESSAGES_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_chat_messages(messages: dict):
    """Save chat messages"""
    CHAT_MESSAGES_JSON.write_text(json.dumps(messages, indent=2), encoding="utf-8")

def create_chat_session(user_email: str, title: str = None) -> str:
    """Create a new chat session and return session_id"""
    import secrets
    session_id = secrets.token_urlsafe(16)
    sessions = load_chat_sessions()
    
    if user_email not in sessions:
        sessions[user_email] = []
    
    sessions[user_email].append({
        "id": session_id,
        "title": title or "New Chat",
        "created_at": datetime.now().isoformat()
    })
    
    save_chat_sessions(sessions)
    return session_id

def get_user_chat_sessions(user_email: str) -> list:
    """Get all chat sessions for a user"""
    sessions = load_chat_sessions()
    return sessions.get(user_email, [])

def add_chat_message(session_id: str, role: str, content: str):
    """Add a message to a chat session"""
    messages_dict = load_chat_messages()
    
    if session_id not in messages_dict:
        messages_dict[session_id] = []
    
    messages_dict[session_id].append({
        "role": role,  # "user" or "assistant"
        "content": content,
        "created_at": datetime.now().isoformat()
    })
    
    save_chat_messages(messages_dict)

def get_chat_messages(session_id: str) -> list:
    """Get all messages for a chat session"""
    messages_dict = load_chat_messages()
    return messages_dict.get(session_id, [])

# ----------------------------
# Subprocess execution
# ----------------------------
def run_cmd(cmd: list, cwd=None):
    """Run a command and return exit code. Passes environment variables including from .env."""
    # Load .env file manually to handle BOM
    env = os.environ.copy()
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            with open(env_file, 'r', encoding='utf-8-sig') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        env[key] = value
        except Exception as e:
            print(f"Warning: Could not load .env file: {e}")
    
    if cwd is None:
        cwd = ROOT
    
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        if result.returncode != 0:
            print(f"Command failed: {' '.join(cmd)}")
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        return result.returncode
    except Exception as e:
        print(f"Error running command {' '.join(cmd)}: {e}")
        return 1

def upload_to_connected_apps(user_email: str, pdf_path: Path, transcript_path: Path, meeting_name: str, providers: list[str] | None = None) -> dict:
    """Upload meeting files to all connected cloud storage apps"""
    users = read_users()
    user = users.get(user_email.lower())
    if not user:
        return {}
    
    connected_apps = user.get("connected_apps", {})
    if providers is None:
        providers = list(connected_apps.keys())

    results: dict[str, dict] = {}
    for provider in providers:
        if provider not in connected_apps:
            results[provider] = {"status": "failed", "error": "Provider not connected"}
    
    # Upload to Dropbox
    if "dropbox" in connected_apps and "dropbox" in providers:
        try:
            print(f"📤 Uploading {meeting_name} to Dropbox...")
            dropbox_config = connected_apps["dropbox"]
            access_token = decrypt_token(dropbox_config["access_token_encrypted"])
            refresh_token = decrypt_token(dropbox_config["refresh_token_encrypted"]) if dropbox_config.get("refresh_token_encrypted") else None
            token_expires_at = dropbox_config.get("token_expires_at")
            
            dropbox_result = upload_to_dropbox(
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=token_expires_at,
                user_email=user_email,
                folder_path=dropbox_config.get("folder_path", "/PhiAI/Meetings"),
                pdf_path=pdf_path,
                transcript_path=transcript_path,
                meeting_name=meeting_name
            )
            print(f"✅ Successfully uploaded {meeting_name} to Dropbox")
            results["dropbox"] = {"status": "success", "details": dropbox_result}
        except Exception as e:
            error_msg = str(e)
            if "expired" in error_msg.lower() or "must reconnect" in error_msg.lower() or "reconnect" in error_msg.lower() or "Action required" in error_msg:
                # Mark Dropbox as needs reauth
                users[user_email.lower()]["connected_apps"]["dropbox"]["needs_reauth"] = True
                write_users(users)
                print(f"❌ Dropbox upload failed: {error_msg}")
                print(f"   → Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again.")
            elif "not configured" in error_msg.lower():
                print(f"❌ Dropbox upload failed: {error_msg}")
            else:
                print(f"❌ Error uploading to Dropbox: {e}")
            # Don't raise - continue with other uploads
            results["dropbox"] = {"status": "failed", "error": error_msg}
    
    # Upload to Google Drive
    if "googledrive" in connected_apps and "googledrive" in providers:
        try:
            print(f"📤 Uploading {meeting_name} to Google Drive...")
            drive_result = upload_to_googledrive(
                access_token=decrypt_token(connected_apps["googledrive"]["access_token_encrypted"]),
                refresh_token=decrypt_token(connected_apps["googledrive"]["refresh_token_encrypted"]) if connected_apps["googledrive"].get("refresh_token_encrypted") else None,
                folder_name=connected_apps["googledrive"].get("folder_name", "PhiAI/Meetings"),
                pdf_path=pdf_path,
                transcript_path=transcript_path,
                meeting_name=meeting_name,
                user_email=user_email
            )
            print(f"✅ Successfully uploaded {meeting_name} to Google Drive")
            results["googledrive"] = {"status": "success", "details": drive_result}
        except Exception as e:
            error_msg = str(e)
            if "expired" in error_msg.lower() or "invalid" in error_msg.lower():
                print(f"❌ Google Drive token expired or invalid. User needs to reconnect Google Drive in account settings.")
            else:
                print(f"❌ Error uploading to Google Drive: {e}")
            results["googledrive"] = {"status": "failed", "error": error_msg}
    
    # Upload to Box
    if "box" in connected_apps and "box" in providers:
        box_config = connected_apps["box"]
        
        # Preflight check: Verify write scope before attempting upload (fail fast)
        from services.box_client import verify_write_scope, BoxInsufficientScopeError, BoxTokenError, get_box_diagnostics
        
        # Check if we already know scopes are bad (fail fast, don't spam Box API)
        if box_config.get("needs_scope_update") or box_config.get("box_write_scope_ok") == False:
            diagnostics = get_box_diagnostics(user_email)
            if diagnostics["status"] == "needs_scopes":
                print(f"❌ Box upload skipped: Write permissions not available (status: needs_scopes)")
                print(f"   → FIX: Go to https://developer.box.com/ → My Apps → Your App → Configuration")
                print(f"   → Enable 'Read and write all files and folders stored in Box' scope")
                print(f"   → Save Changes, wait 2-3 minutes, then reconnect Box in Settings → Connected Apps")
                # Don't attempt upload - fail fast
                return
        
        # Verify write scope (uses cache if recent, won't spam API)
        has_write, scope_error = verify_write_scope(user_email, force_check=False)
        if not has_write:
            print(f"❌ Box upload skipped: Write permissions verification failed")
            if scope_error:
                print(f"   → {scope_error[:150]}")
            print(f"   → FIX: Go to https://developer.box.com/ → My Apps → Your App → Configuration")
            print(f"   → Enable 'Read and write all files and folders stored in Box' scope")
            print(f"   → Save Changes, wait 2-3 minutes, then reconnect Box in Settings → Connected Apps")
            # Don't attempt upload - fail fast
            return
        
        try:
            print(f"📤 Uploading {meeting_name} to Box...")
            access_token = decrypt_token(box_config["access_token_encrypted"])
            refresh_token = decrypt_token(box_config["refresh_token_encrypted"]) if box_config.get("refresh_token_encrypted") else None
            token_expires_at = box_config.get("token_expires_at")
            
            box_result = upload_to_box(
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=token_expires_at,
                user_email=user_email,
                folder_name=box_config.get("folder_name", "PhiAI/Meetings"),
                pdf_path=pdf_path,
                transcript_path=transcript_path,
                meeting_name=meeting_name
            )
            print(f"✅ Successfully uploaded {meeting_name} to Box")
            results["box"] = {"status": "success", "details": box_result}
        except BoxInsufficientScopeError as e:
            # Mark Box as needing scope update (not just reauth)
            users[user_email.lower()]["connected_apps"]["box"]["needs_scope_update"] = True
            write_users(users)
            print(f"❌ Box upload failed: {e}")
            print(f"   → Developer action: Configure scopes in Box Developer Console")
            print(f"   → User action: After scopes are configured, go to Settings → Connected Apps → Box → Disconnect → Connect again")
            results["box"] = {"status": "failed", "error": str(e)}
        except BoxTokenError as e:
            # Mark Box as needing reauth
            users[user_email.lower()]["connected_apps"]["box"]["needs_reauth"] = True
            write_users(users)
            print(f"❌ Box upload failed: {e}")
            print(f"   → Action required: go to Settings → Connected Apps → Box → Disconnect, then Connect again.")
            results["box"] = {"status": "failed", "error": str(e)}
        except Exception as e:
            error_msg = str(e)
            if "not installed" in error_msg.lower() or "ImportError" in error_msg or "Developer action required" in error_msg:
                print(f"❌ {error_msg}")
                # Mark as needs reauth if SDK missing (user can't fix this, but mark it anyway)
                if "not installed" in error_msg.lower() or "ImportError" in error_msg:
                    users[user_email.lower()]["connected_apps"]["box"]["needs_reauth"] = True
                    write_users(users)
            elif "not configured" in error_msg.lower():
                print(f"❌ Box upload failed: {error_msg}")
            else:
                print(f"❌ Error uploading to Box: {e}")
            # Don't raise - continue with other uploads
            results["box"] = {"status": "failed", "error": error_msg}

    if results:
        print(f"📦 Upload summary for {user_email}:")
        for provider, info in results.items():
            status = info.get("status", "unknown")
            if status == "success":
                print(f"   ✅ {provider}: success {info.get('details')}")
            else:
                print(f"   ❌ {provider}: {info.get('error', 'failed')}")

    return results

# ----------------------------
# Background upload jobs
# ----------------------------
UPLOAD_WORKER_STARTED = False

# ----------------------------
# Meeting processing jobs (upload/transcription progress)
# Stored in output/jobs/meetings/<meeting_id>.json + .log
# ----------------------------

def _meeting_job_paths(meeting_id: str) -> tuple[Path, Path]:
    ensure_dirs()
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (meeting_id or "unknown")).strip("_") or "unknown"
    job_path = MEETING_JOBS_DIR / f"{safe_id}.json"
    log_path = MEETING_JOBS_DIR / f"{safe_id}.log"
    return job_path, log_path

def _load_meeting_job(meeting_id: str) -> dict | None:
    job_path, _ = _meeting_job_paths(meeting_id)
    if not job_path.exists():
        return None
    try:
        return json.loads(job_path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _save_meeting_job(meeting_id: str, job: dict) -> None:
    job_path, _ = _meeting_job_paths(meeting_id)
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

def _append_meeting_job_log(meeting_id: str, line: str) -> None:
    _, log_path = _meeting_job_paths(meeting_id)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write((line or "").rstrip("\n") + "\n")
    except Exception:
        pass

def _upsert_meeting_job(
    meeting_id: str,
    *,
    meeting_name: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    percent: int | None = None,
    transcription_percent: int | None = None,
    transcribed_seconds: float | None = None,
    total_audio_seconds: float | None = None,
    error: str | None = None,
) -> dict:
    now = datetime.now().isoformat()
    existing = _load_meeting_job(meeting_id) or {}
    job_path, log_path = _meeting_job_paths(meeting_id)
    job = {
        "kind": "meeting_processing",
        "meeting_id": meeting_id,
        "meeting_name": meeting_name or existing.get("meeting_name") or meeting_id,
        "status": status or existing.get("status") or "queued",  # queued|uploading|processing|done|failed
        "stage": stage or existing.get("stage") or "queued",
        "percent": int(percent) if percent is not None else int(existing.get("percent") or 0),
        "transcription_percent": int(transcription_percent) if transcription_percent is not None else existing.get("transcription_percent"),
        "transcribed_seconds": float(transcribed_seconds) if transcribed_seconds is not None else existing.get("transcribed_seconds"),
        "total_audio_seconds": float(total_audio_seconds) if total_audio_seconds is not None else existing.get("total_audio_seconds"),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "error": error if error is not None else existing.get("error"),
        "job_path": str(job_path),
        "log_path": str(log_path),
    }
    # Clamp
    job["percent"] = max(0, min(100, int(job.get("percent") or 0)))
    try:
        if job.get("transcription_percent") is not None:
            job["transcription_percent"] = max(0, min(100, int(job.get("transcription_percent") or 0)))
    except Exception:
        pass
    _save_meeting_job(meeting_id, job)
    return job

def _list_active_meeting_jobs() -> list[dict]:
    ensure_dirs()
    jobs: list[dict] = []
    for p in MEETING_JOBS_DIR.glob("*.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if j.get("kind") != "meeting_processing":
            continue
        if j.get("status") in ("queued", "uploading", "processing"):
            jobs.append(j)
    # Most recently updated first
    jobs.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    return jobs

def _read_tail_lines(path: Path, max_lines: int = 200) -> list[str]:
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max_lines:]
    except Exception:
        return []

def _load_upload_job(job_path: Path) -> dict:
    return json.loads(job_path.read_text(encoding="utf-8"))

def _save_upload_job(job_path: Path, job: dict) -> None:
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

def _list_upload_jobs() -> list[Path]:
    if not UPLOAD_JOBS_DIR.exists():
        return []
    return sorted(UPLOAD_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)

def enqueue_upload_job(meeting_name: str, pdf_path: Path, transcript_path: Path, participant_emails: list[str]) -> str | None:
    ensure_dirs()
    users = read_users()
    job_users = []

    for email in participant_emails:
        user = users.get(email.lower())
        if not user:
            continue
        connected_apps = user.get("connected_apps", {})
        providers = [p for p in ("dropbox", "googledrive", "box") if p in connected_apps]
        if not providers:
            continue
        provider_status = {
            p: {"state": "pending", "attempts": 0, "last_error": None, "result": None}
            for p in providers
        }
        job_users.append({
            "email": email.lower(),
            "providers": providers,
            "provider_status": provider_status
        })

    if not job_users:
        print("[INFO] No connected apps found for any participants; skipping upload job.")
        return None

    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "meeting_name": meeting_name,
        "pdf_path": str(pdf_path),
        "transcript_path": str(transcript_path),
        "users": job_users,
        "max_attempts": 3
    }

    job_path = UPLOAD_JOBS_DIR / f"{job_id}.json"
    _save_upload_job(job_path, job)
    print(f"[INFO] Enqueued upload job {job_id} for {meeting_name}")
    return job_id

def _should_retry_upload_error(error_msg: str) -> bool:
    msg = (error_msg or "").lower()
    if any(key in msg for key in ["action required", "reconnect", "not configured", "insufficient", "scope", "permission"]):
        return False
    if any(key in msg for key in ["timeout", "temporar", "connection", "rate limit", "503", "504", "network"]):
        return True
    return True

def _process_upload_job(job_path: Path) -> None:
    job = _load_upload_job(job_path)
    if job.get("status") in ("completed", "failed"):
        return

    job["status"] = "in_progress"
    _save_upload_job(job_path, job)

    meeting_name = job.get("meeting_name", "meeting")
    pdf_path = Path(job.get("pdf_path", ""))
    transcript_path = Path(job.get("transcript_path", ""))
    max_attempts = int(job.get("max_attempts", 3))

    for user_entry in job.get("users", []):
        email = user_entry.get("email")
        providers = user_entry.get("providers", [])
        provider_status = user_entry.get("provider_status", {})
        for provider in providers:
            state = provider_status.get(provider, {})
            if state.get("state") == "success":
                continue
            attempts = int(state.get("attempts", 0))
            if attempts >= max_attempts:
                continue

            print(f"[UPLOAD JOB] {job['id']} -> {email} -> {provider} (attempt {attempts + 1}/{max_attempts})")
            try:
                result = upload_to_connected_apps(
                    email,
                    pdf_path,
                    transcript_path,
                    meeting_name,
                    providers=[provider]
                )
                provider_result = result.get(provider) if isinstance(result, dict) else None
                if provider_result and provider_result.get("status") == "success":
                    provider_status[provider] = {
                        "state": "success",
                        "attempts": attempts + 1,
                        "last_error": None,
                        "result": provider_result
                    }
                else:
                    error_msg = (provider_result or {}).get("error", "Unknown upload failure")
                    retry = _should_retry_upload_error(error_msg)
                    provider_status[provider] = {
                        "state": "retry" if retry else "failed",
                        "attempts": attempts + 1,
                        "last_error": error_msg,
                        "result": provider_result
                    }
                    if not retry:
                        provider_status[provider]["attempts"] = max_attempts
            except Exception as e:
                error_msg = str(e)
                retry = _should_retry_upload_error(error_msg)
                provider_status[provider] = {
                    "state": "retry" if retry else "failed",
                    "attempts": attempts + 1,
                    "last_error": error_msg,
                    "result": None
                }
                if not retry:
                    provider_status[provider]["attempts"] = max_attempts
                print(f"[UPLOAD JOB] ❌ {provider} failed: {error_msg}")

            user_entry["provider_status"] = provider_status
            _save_upload_job(job_path, job)

    # Finalize job status
    all_done = True
    any_success = False
    for user_entry in job.get("users", []):
        for provider in user_entry.get("providers", []):
            state = user_entry.get("provider_status", {}).get(provider, {})
            if state.get("state") == "success":
                any_success = True
            if state.get("state") not in ("success", "failed"):
                all_done = False

    if all_done:
        job["status"] = "completed" if any_success else "failed"
        _save_upload_job(job_path, job)

        print(f"[UPLOAD JOB] Summary for {job.get('meeting_name')}:")
        for user_entry in job.get("users", []):
            email = user_entry.get("email")
            print(f"  User: {email}")
            for provider in user_entry.get("providers", []):
                state = user_entry.get("provider_status", {}).get(provider, {})
                if state.get("state") == "success":
                    print(f"   ✅ {provider}: {state.get('result')}")
                else:
                    print(f"   ❌ {provider}: {state.get('last_error')}")

def _upload_worker_loop() -> None:
    print("[UPLOAD WORKER] Started background upload worker.")
    while True:
        try:
            jobs = _list_upload_jobs()
            for job_path in jobs:
                _process_upload_job(job_path)
        except Exception as e:
            print(f"[UPLOAD WORKER] Error processing jobs: {e}")
        time.sleep(3)

def start_upload_worker() -> None:
    global UPLOAD_WORKER_STARTED
    if UPLOAD_WORKER_STARTED or os.getenv("DIO_DISABLE_UPLOAD_WORKER") == "1":
        return
    UPLOAD_WORKER_STARTED = True
    worker_thread = threading.Thread(target=_upload_worker_loop, daemon=True)
    worker_thread.start()

def refresh_dropbox_token(user_email: str, refresh_token: str) -> tuple[str, str, int] | None:
    """
    Refresh Dropbox access token using refresh token (token rotation).
    
    Returns:
        Tuple of (new_access_token, new_refresh_token, new_expires_at) if refresh succeeded, None if failed
    """
    try:
        DROPBOX_CLIENT_ID = os.getenv("DROPBOX_CLIENT_ID") or os.getenv("DROPBOX_APP_KEY")
        DROPBOX_CLIENT_SECRET = os.getenv("DROPBOX_CLIENT_SECRET") or os.getenv("DROPBOX_APP_SECRET")
        
        if not DROPBOX_CLIENT_ID or not DROPBOX_CLIENT_SECRET:
            print(f"[WARN] Dropbox credentials not configured, cannot refresh token for {user_email}")
            return None
        
        if not refresh_token:
            print(f"[WARN] No refresh token available for Dropbox user {user_email}")
            return None
        
        print(f"[INFO] Refreshing Dropbox token for {user_email}...")
        
        # Dropbox token rotation endpoint
        token_response = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": DROPBOX_CLIENT_ID,
                "client_secret": DROPBOX_CLIENT_SECRET
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10
        )
        
        if token_response.status_code != 200:
            error_data = token_response.json() if token_response.text else {}
            error_msg = error_data.get("error_description", error_data.get("error", "Unknown error"))
            print(f"[ERROR] Dropbox token refresh failed: {error_msg}")
            return None
        
        token_data = token_response.json()
        new_access_token = token_data.get("access_token")
        new_refresh_token = token_data.get("refresh_token")  # May be same or new refresh token
        expires_in = token_data.get("expires_in", 14400)  # Default 4 hours
        
        if not new_access_token:
            print(f"[ERROR] No access token in Dropbox refresh response")
            return None
        
        # Use new refresh token if provided, otherwise keep the old one
        final_refresh_token = new_refresh_token if new_refresh_token else refresh_token
        new_expires_at = int(time.time()) + expires_in - 120  # Subtract 2 min buffer
        
        print(f"[SUCCESS] Dropbox token refreshed successfully (expires in {expires_in}s)")
        
        # Update stored tokens in database
        try:
            users = read_users()
            if user_email.lower() in users:
                if "connected_apps" not in users[user_email.lower()]:
                    users[user_email.lower()]["connected_apps"] = {}
                if "dropbox" not in users[user_email.lower()]["connected_apps"]:
                    users[user_email.lower()]["connected_apps"]["dropbox"] = {}
                
                users[user_email.lower()]["connected_apps"]["dropbox"]["access_token_encrypted"] = encrypt_token(new_access_token)
                users[user_email.lower()]["connected_apps"]["dropbox"]["refresh_token_encrypted"] = encrypt_token(final_refresh_token)
                users[user_email.lower()]["connected_apps"]["dropbox"]["token_expires_at"] = new_expires_at
                write_users(users)
                print(f"[SUCCESS] Dropbox tokens updated in database")
        except Exception as e:
            print(f"[WARN] Failed to store refreshed Dropbox tokens: {e}")
        
        return (new_access_token, final_refresh_token, new_expires_at)
    except Exception as e:
        print(f"[ERROR] Exception refreshing Dropbox token: {e}")
        import traceback
        traceback.print_exc()
        return None


def upload_to_dropbox(access_token: str, refresh_token: str | None, token_expires_at: int | None, user_email: str, folder_path: str, pdf_path: Path, transcript_path: Path, meeting_name: str):
    """Upload files to Dropbox with automatic token refresh"""
    try:
        import dropbox
        from dropbox.exceptions import AuthError
        
        DROPBOX_CLIENT_ID = os.getenv("DROPBOX_CLIENT_ID") or os.getenv("DROPBOX_APP_KEY")
        DROPBOX_CLIENT_SECRET = os.getenv("DROPBOX_CLIENT_SECRET") or os.getenv("DROPBOX_APP_SECRET")
        
        if not DROPBOX_CLIENT_ID or not DROPBOX_CLIENT_SECRET:
            raise Exception("Dropbox credentials (DROPBOX_CLIENT_ID/DROPBOX_APP_KEY, DROPBOX_CLIENT_SECRET/DROPBOX_APP_SECRET) not configured in .env file. Developer action required: set DROPBOX_CLIENT_ID and DROPBOX_CLIENT_SECRET in the server .env and restart.")
        
        # Check if token needs refresh (expiring soon or already expired)
        current_time = int(time.time())
        needs_refresh = False
        if token_expires_at:
            time_until_expiry = token_expires_at - current_time
            if time_until_expiry <= 120:  # Less than 2 minutes remaining or expired
                needs_refresh = True
                print(f"[INFO] Dropbox token expires soon (in {time_until_expiry}s), attempting refresh...")
        elif not token_expires_at:
            # No expiration stored - try a lightweight API call to check if token is valid
            # If it fails with 401, we'll attempt refresh
            pass
        
        # Attempt refresh if needed
        if needs_refresh and refresh_token:
            refresh_result = refresh_dropbox_token(user_email, refresh_token)
            if refresh_result:
                access_token, refresh_token, token_expires_at = refresh_result
                print(f"[SUCCESS] Dropbox token refreshed before upload")
            else:
                # Refresh failed - check if we have required credentials
                if not refresh_token:
                    raise Exception("Dropbox upload failed because your Dropbox token is expired and we don't have a refresh token to refresh it. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again. After reconnecting, run one test upload to confirm.")
                elif not DROPBOX_CLIENT_ID or not DROPBOX_CLIENT_SECRET:
                    raise Exception("Dropbox upload failed because your Dropbox token is expired and we don't have app credentials to refresh it. Developer action required: set DROPBOX_CLIENT_ID and DROPBOX_CLIENT_SECRET in the server .env and restart. User action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again.")
                else:
                    raise Exception("Dropbox upload failed because your Dropbox token is expired and refresh failed. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again. After reconnecting, run one test upload to confirm.")
        
        # Use oauth2_access_token parameter and provide app credentials
        # Dropbox SDK may automatically refresh token if needed (when app_key/app_secret provided)
        dbx = dropbox.Dropbox(
            oauth2_access_token=access_token,
            app_key=DROPBOX_CLIENT_ID,
            app_secret=DROPBOX_CLIENT_SECRET
        )
        
        # Test connection by getting account info first
        # This may trigger SDK auto-refresh if token is close to expiring
        try:
            dbx.users_get_current_account()
            print(f"[SUCCESS] Dropbox connection verified")
        except AuthError as auth_err:
            # Token expired or invalid - try refresh if we have refresh_token
            error_msg = str(auth_err)
            print(f"[ERROR] Dropbox authentication failed: {error_msg}")
            
            # If we have a refresh token, try refreshing now
            if refresh_token and ("expired" in error_msg.lower() or "expired_access_token" in error_msg.lower() or "invalid_access_token" in error_msg.lower()):
                print(f"[INFO] Dropbox token expired during upload, attempting refresh and retry...")
                refresh_result = refresh_dropbox_token(user_email, refresh_token)
                if refresh_result:
                    access_token, refresh_token, token_expires_at = refresh_result
                    # Retry with new token
                    dbx = dropbox.Dropbox(
                        oauth2_access_token=access_token,
                        app_key=DROPBOX_CLIENT_ID,
                        app_secret=DROPBOX_CLIENT_SECRET
                    )
                    dbx.users_get_current_account()
                    print(f"[SUCCESS] Dropbox connection verified after refresh")
                else:
                    # Refresh failed - provide clear instructions
                    if not refresh_token:
                        raise Exception("Dropbox upload failed because your Dropbox token is expired and we don't have a refresh token to refresh it. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again. After reconnecting, run one test upload to confirm.")
                    elif not DROPBOX_CLIENT_ID or not DROPBOX_CLIENT_SECRET:
                        raise Exception("Dropbox upload failed because your Dropbox token is expired and we don't have app credentials to refresh it. Developer action required: set DROPBOX_CLIENT_ID and DROPBOX_CLIENT_SECRET in the server .env and restart. User action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again.")
                    else:
                        raise Exception("Dropbox upload failed because your Dropbox token is expired and refresh failed. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again. After reconnecting, run one test upload to confirm.")
            else:
                # No refresh token or different error
                if "expired" in error_msg.lower() or "expired_access_token" in error_msg.lower() or "invalid_access_token" in error_msg.lower():
                    if not refresh_token:
                        raise Exception("Dropbox upload failed because your Dropbox token is expired and we don't have a refresh token to refresh it. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again. After reconnecting, run one test upload to confirm.")
                    else:
                        raise Exception("Dropbox access token is expired and cannot be refreshed. User must reconnect Dropbox in account settings.")
                else:
                    raise Exception(f"Dropbox authentication failed: {error_msg}")
        
        # Format meeting name for folder structure (match Google Drive: YYYY/MM/DD)
        formatted_meeting_name = format_meeting_name_for_drive(meeting_name)
        
        # Build folder path: /PhiAI Meetings/meeting YYYY/MM/DD/
        # Dropbox paths use / as separator
        base_folder = folder_path.rstrip('/')
        meeting_folder_path = f"{base_folder}/meeting {formatted_meeting_name}".rstrip('/')
        
        # Ensure folder path starts with /
        if not meeting_folder_path.startswith("/"):
            meeting_folder_path = "/" + meeting_folder_path
        
        # Create nested folder structure if it doesn't exist
        # Split path into parts and create each level
        parts = meeting_folder_path.strip('/').split('/')
        current_path = ""
        for part in parts:
            current_path = current_path + "/" + part if current_path else "/" + part
            try:
                dbx.files_get_metadata(current_path)
            except dropbox.exceptions.ApiError as e:
                if e.error.is_path() and e.error.get_path().is_not_found():
                    # Folder doesn't exist, create it
                    try:
                        dbx.files_create_folder_v2(current_path)
                        print(f"  [INFO] Created Dropbox folder: {current_path}")
                    except dropbox.exceptions.ApiError as create_err:
                        # May fail if created by another process, check again
                        try:
                            dbx.files_get_metadata(current_path)
                        except:
                            raise Exception(f"Failed to create Dropbox folder {current_path}: {create_err}")
        
        upload_results = {"pdf": None, "transcript": None}
        safe_meeting_name = meeting_name.replace("/", "-").strip()

        # Upload PDF (meeting report)
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            with open(pdf_path, 'rb') as f:
                file_data = f.read()
                pdf_remote_path = f"{meeting_folder_path}/{safe_meeting_name}_meeting_report.pdf"
                dbx.files_upload(
                    file_data,
                    pdf_remote_path,
                    mode=dropbox.files.WriteMode.overwrite
                )
                upload_results["pdf"] = {"path": pdf_remote_path, "bytes": len(file_data)}
                print(f"  ✓ Uploaded PDF to Dropbox: {pdf_remote_path} ({len(file_data)} bytes)")
        else:
            print(f"  ⚠️  PDF not found or empty at {pdf_path}, skipping PDF upload to Dropbox")
        
        # Upload transcript (named script)
        if transcript_path.exists() and transcript_path.stat().st_size > 0:
            with open(transcript_path, 'rb') as f:
                file_data = f.read()
                transcript_remote_path = f"{meeting_folder_path}/{safe_meeting_name}_named_script.txt"
                dbx.files_upload(
                    file_data,
                    transcript_remote_path,
                    mode=dropbox.files.WriteMode.overwrite
                )
                upload_results["transcript"] = {"path": transcript_remote_path, "bytes": len(file_data)}
                print(f"  ✓ Uploaded transcript to Dropbox: {transcript_remote_path} ({len(file_data)} bytes)")
        else:
            print(f"  ⚠️  Transcript not found or empty at {transcript_path}, skipping transcript upload to Dropbox")

        return upload_results
    except AuthError as e:
        error_msg = str(e)
        if "expired" in error_msg.lower() or "expired_access_token" in error_msg.lower() or "invalid_access_token" in error_msg.lower():
            # Try one more refresh attempt if we have refresh_token
            if refresh_token:
                print(f"[INFO] Dropbox AuthError during upload, attempting final refresh...")
                refresh_result = refresh_dropbox_token(user_email, refresh_token)
                if refresh_result:
                    # Retry upload would require recursive call - instead raise with clear message
                    raise Exception("Dropbox token expired during upload. Token was refreshed, but upload needs to be retried. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again to ensure refresh tokens are properly stored.")
                else:
                    raise Exception("Dropbox upload failed because your Dropbox token is expired and we don't have a refresh token (or app credentials) to refresh it. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again. After reconnecting, run one test upload to confirm.")
            else:
                raise Exception("Dropbox upload failed because your Dropbox token is expired and we don't have a refresh token to refresh it. Action required: go to Settings → Connected Apps → Dropbox → Disconnect, then Connect again. After reconnecting, run one test upload to confirm.")
        else:
            raise Exception(f"Dropbox authentication failed: {error_msg}")
    except Exception as e:
        print(f"[ERROR] Dropbox upload error: {e}")
        import traceback
        if os.getenv("FLASK_DEBUG") == "1":
            traceback.print_exc()
        raise

def format_meeting_name_for_drive(meeting_name: str) -> str:
    """Format meeting name with / separated dates for cleaner Google Drive naming"""
    import re
    # Pattern to match YYYYMMDD format (8 digits)
    date_pattern = r'(\d{4})(\d{2})(\d{2})'
    
    # Replace YYYYMMDD with YYYY/MM/DD
    formatted = re.sub(date_pattern, r'\1/\2/\3', meeting_name)
    
    # Also replace underscores with spaces for readability (but keep / for dates)
    formatted = formatted.replace('_', ' ')
    
    return formatted

def upload_to_googledrive(access_token: str, refresh_token: str | None, folder_name: str, pdf_path: Path, transcript_path: Path, meeting_name: str, user_email: str | None = None):
    """Upload files to Google Drive"""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request
        
        GOOGLE_CLIENT_ID = os.getenv("GOOGLE_DRIVE_CLIENT_ID")
        GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET")
        
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            raise Exception("Google Drive credentials not configured")
        
        # Create credentials
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET
        )
        
        # Refresh token if needed
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if user_email:
                users = read_users()
                if user_email.lower() in users:
                    if "connected_apps" not in users[user_email.lower()]:
                        users[user_email.lower()]["connected_apps"] = {}
                    if "googledrive" not in users[user_email.lower()]["connected_apps"]:
                        users[user_email.lower()]["connected_apps"]["googledrive"] = {}
                    users[user_email.lower()]["connected_apps"]["googledrive"]["access_token_encrypted"] = encrypt_token(creds.token)
                    users[user_email.lower()]["connected_apps"]["googledrive"]["token_expires_at"] = int(creds.expiry.timestamp()) if creds.expiry else None
                    write_users(users)
        
        service = build('drive', 'v3', credentials=creds)
        
        def get_or_create_folder(name: str, parent_id: str | None) -> str:
            parent_clause = f" and '{parent_id}' in parents" if parent_id else ""
            query = (
                f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false{parent_clause}"
            )
            results = service.files().list(q=query, spaces='drive').execute()
            items = results.get('files', [])
            if items:
                return items[0]["id"]
            file_metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                file_metadata["parents"] = [parent_id]
            folder = service.files().create(body=file_metadata, fields="id").execute()
            return folder.get("id")
        
        # Build folder path: /PhiAI/Meetings/meeting YYYY/MM/DD/
        clean_meeting_name = format_meeting_name_for_drive(meeting_name)
        base_parts = [p for p in folder_name.replace("\\", "/").split("/") if p]
        meeting_parts = [p for p in clean_meeting_name.split("/") if p]
        if meeting_parts:
            meeting_parts[0] = f"meeting {meeting_parts[0].strip()}"
        folder_parts = base_parts + meeting_parts
        folder_id = None
        for part in folder_parts:
            folder_id = get_or_create_folder(part, folder_id)
        
        upload_results = {"pdf": None, "transcript": None}
        safe_meeting_name = meeting_name.replace("/", "-").strip()

        def upload_or_update(file_path: Path, filename: str, mime_type: str):
            existing_query = f"name='{filename}' and parents in '{folder_id}' and trashed=false"
            existing_results = service.files().list(q=existing_query, spaces='drive').execute()
            existing_files = existing_results.get('files', [])
            file_metadata = {"name": filename, "parents": [folder_id]}
            media = MediaFileUpload(str(file_path), mimetype=mime_type)
            if existing_files:
                file_id = existing_files[0]["id"]
                return service.files().update(fileId=file_id, body=file_metadata, media_body=media, fields="id").execute()
            return service.files().create(body=file_metadata, media_body=media, fields="id").execute()

        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            pdf_filename = f"{safe_meeting_name}_meeting_report.pdf"
            file = upload_or_update(pdf_path, pdf_filename, "application/pdf")
            upload_results["pdf"] = {"id": file.get("id"), "name": pdf_filename}
            print(f"  ✓ Uploaded PDF to Google Drive: {folder_name}/{pdf_filename} (ID: {file.get('id')})")
        else:
            print(f"  ⚠️  PDF not found or empty at {pdf_path}, skipping PDF upload to Google Drive")

        if transcript_path.exists() and transcript_path.stat().st_size > 0:
            transcript_filename = f"{safe_meeting_name}_named_script.txt"
            file = upload_or_update(transcript_path, transcript_filename, "text/plain")
            upload_results["transcript"] = {"id": file.get("id"), "name": transcript_filename}
            print(f"  ✓ Uploaded transcript to Google Drive: {folder_name}/{transcript_filename} (ID: {file.get('id')})")
        else:
            print(f"  ⚠️  Transcript not found or empty at {transcript_path}, skipping transcript upload to Google Drive")

        return upload_results
    except Exception as e:
        print(f"Google Drive upload error: {e}")
        raise

def refresh_box_token(user_email: str, refresh_token: str) -> tuple[str, str, int] | None:
    """
    Refresh Box access token using refresh token.
    
    Returns:
        Tuple of (new_access_token, new_refresh_token, expires_at_timestamp) or None if refresh failed
    """
    try:
        BOX_CLIENT_ID = os.getenv("BOX_CLIENT_ID")
        BOX_CLIENT_SECRET = os.getenv("BOX_CLIENT_SECRET")
        
        if not BOX_CLIENT_ID or not BOX_CLIENT_SECRET:
            print(f"[WARN] Box credentials not configured, cannot refresh token for {user_email}")
            return None
        
        if not refresh_token:
            print(f"[WARN] No refresh token available for Box user {user_email}")
            return None
        
        print(f"[INFO] Refreshing Box token for {user_email}...")
        
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
            print(f"[ERROR] Box token refresh failed: {error_msg}")
            return None
        
        token_data = token_response.json()
        new_access_token = token_data.get("access_token")
        new_refresh_token = token_data.get("refresh_token", refresh_token)  # May rotate or stay same
        expires_in = token_data.get("expires_in", 3600)  # Default 1 hour (3600 seconds)
        
        if not new_access_token:
            print(f"[ERROR] No access token in Box refresh response")
            return None
        
        # Calculate new expiration (with 2 min buffer)
        new_expires_at = int(time.time()) + expires_in - 120
        
        print(f"[SUCCESS] Box token refreshed successfully (expires in {expires_in}s)")
        
        # Update stored tokens atomically
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
        
        return (new_access_token, new_refresh_token, new_expires_at)
        
    except Exception as e:
        print(f"[ERROR] Exception refreshing Box token: {e}")
        import traceback
        if os.getenv("FLASK_DEBUG") == "1":
            traceback.print_exc()
        return None


def upload_to_box(access_token: str, refresh_token: str | None, token_expires_at: int | None, user_email: str, folder_name: str, pdf_path: Path, transcript_path: Path, meeting_name: str):
    """Upload files to Box with automatic token refresh and scope verification"""
    from services.box_client import (
        get_authenticated_client, 
        verify_write_scope, 
        BoxInsufficientScopeError,
        BoxTokenError
    )
    from boxsdk.exception import BoxAPIException
    
    try:
        print(f"[Box] Starting upload for {meeting_name}...")
        
        # Verify write scope before attempting upload (cached check)
        has_write_scope, scope_error = verify_write_scope(user_email, force_check=False)
        if not has_write_scope:
            # Mark connection as needing scope update
            users = read_users()
            if user_email.lower() in users:
                if "connected_apps" not in users[user_email.lower()]:
                    users[user_email.lower()]["connected_apps"] = {}
                if "box" not in users[user_email.lower()]["connected_apps"]:
                    users[user_email.lower()]["connected_apps"]["box"] = {}
                users[user_email.lower()]["connected_apps"]["box"]["needs_scope_update"] = True
                write_users(users)
            
            raise BoxInsufficientScopeError(
                scope_error or "Box token lacks write permissions. "
                "Developer action: In Box Developer Console → My Apps → Your App → Configuration → Application Scopes, "
                "enable 'Read and write all files and folders stored in Box'. "
                "User action: After enabling scopes, go to Settings → Connected Apps → Box → Disconnect → Connect again. "
                "Changes can take a few minutes to propagate."
            )
        
        # Get authenticated client (handles token refresh automatically)
        client = get_authenticated_client(user_email)
        if not client:
            raise BoxTokenError("Failed to create authenticated Box client")
        
        print(f"[Box] Token valid, write scope verified")
        
        # Format meeting name for folder structure (match Google Drive: YYYY/MM/DD)
        formatted_meeting_name = format_meeting_name_for_drive(meeting_name)
        
        # Build folder structure: PhiAI Meetings/meeting YYYY/MM/DD/
        base_folder_name = folder_name
        meeting_folder_path = f"{base_folder_name}/meeting {formatted_meeting_name}"
        
        # Get root folder
        root_folder = client.folder('0')
        print(f"[Box] Creating folder path: {meeting_folder_path}")
        
        # Find or create nested folder structure
        current_folder = root_folder
        folder_parts = meeting_folder_path.split('/')
        
        for folder_part in folder_parts:
            if not folder_part.strip():
                continue
            
            # Look for folder in current location
            folder_id = None
            try:
                items = list(current_folder.get_items())
                for item in items:
                    if item.type == 'folder' and item.name == folder_part:
                        folder_id = item.id
                        break
                
                if folder_id:
                    current_folder = client.folder(folder_id)
                    print(f"[Box] Using existing folder: {folder_part}")
                else:
                    # Create folder
                    new_folder = current_folder.create_subfolder(folder_part)
                    current_folder = new_folder
                    print(f"[Box] Created folder: {folder_part}")
            except BoxAPIException as e:
                if e.status == 409:  # Conflict - folder already exists (race condition)
                    # Find it again
                    items = list(current_folder.get_items())
                    for item in items:
                        if item.type == 'folder' and item.name == folder_part:
                            current_folder = client.folder(item.id)
                            print(f"[Box] Found folder after race condition: {folder_part}")
                            break
                    else:
                        raise Exception(f"Failed to find or create Box folder {folder_part}: {e}")
                elif e.status == 403:
                    # Insufficient scope - token doesn't have required permissions
                    error_msg = str(e)
                    if "insufficient_scope" in error_msg.lower() or "requires higher privileges" in error_msg.lower():
                        # Mark connection as needing scope update
                        users = read_users()
                        if user_email.lower() in users:
                            if "connected_apps" not in users[user_email.lower()]:
                                users[user_email.lower()]["connected_apps"] = {}
                            if "box" not in users[user_email.lower()]["connected_apps"]:
                                users[user_email.lower()]["connected_apps"]["box"] = {}
                            users[user_email.lower()]["connected_apps"]["box"]["needs_scope_update"] = True
                            write_users(users)
                        
                        raise BoxInsufficientScopeError(
                            "Box upload failed: Your Box access token doesn't have write permissions. "
                            "Developer action: In Box Developer Console → My Apps → Your App → Configuration → Application Scopes, "
                            "enable 'Read and write all files and folders stored in Box'. "
                            "User action: After enabling scopes, go to Settings → Connected Apps → Box → Disconnect → Connect again. "
                            "Changes can take a few minutes to propagate."
                        )
                    else:
                        raise Exception(f"Box upload failed due to insufficient permissions (403): {e}")
                else:
                    raise Exception(f"Failed to create Box folder {folder_part}: {e}")
        
        upload_results = {"pdf": None, "transcript": None}
        safe_meeting_name = meeting_name.replace("/", "-").strip()

        # Upload PDF (meeting report)
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            file_size = pdf_path.stat().st_size
            pdf_filename = f"{safe_meeting_name}_meeting_report.pdf"
            print(f"[Box] Uploading PDF: {pdf_filename} ({file_size} bytes)")
            
            # Check if file already exists and overwrite/version it
            try:
                existing_files = list(current_folder.get_items())
                existing_file_id = None
                for item in existing_files:
                    if item.type == 'file' and item.name == pdf_filename:
                        existing_file_id = item.id
                        break
                
                if existing_file_id:
                    # Upload new version
                    with open(pdf_path, 'rb') as f:
                        file = client.file(existing_file_id).update_contents_with_stream(
                            f,
                            etag=None  # Force new version
                        )
                    upload_results["pdf"] = {"id": file.id, "name": pdf_filename}
                    print(f"[Box] Uploaded PDF (new version): {meeting_folder_path}/{pdf_filename} (ID: {file.id}, {file_size} bytes)")
                else:
                    # Upload new file
                    with open(pdf_path, 'rb') as f:
                        file = current_folder.upload_stream(f, pdf_filename)
                    # Verify upload succeeded
                    if file and file.id:
                        upload_results["pdf"] = {"id": file.id, "name": pdf_filename}
                        print(f"[Box] Uploaded PDF: {meeting_folder_path}/{pdf_filename} (ID: {file.id}, {file_size} bytes)")
                    else:
                        raise Exception("Box upload completed but file object is invalid")
            except BoxAPIException as upload_err:
                if upload_err.status == 403:
                    error_msg = str(upload_err)
                    if "insufficient_scope" in error_msg.lower():
                        # Mark connection as needing scope update
                        users = read_users()
                        if user_email.lower() in users:
                            if "connected_apps" not in users[user_email.lower()]:
                                users[user_email.lower()]["connected_apps"] = {}
                            if "box" not in users[user_email.lower()]["connected_apps"]:
                                users[user_email.lower()]["connected_apps"]["box"] = {}
                            users[user_email.lower()]["connected_apps"]["box"]["needs_scope_update"] = True
                            write_users(users)
                        
                        raise BoxInsufficientScopeError(
                            "Box upload failed: Your Box access token doesn't have write permissions. "
                            "Developer action: In Box Developer Console → My Apps → Your App → Configuration → Application Scopes, "
                            "enable 'Read and write all files and folders stored in Box'. "
                            "User action: After enabling scopes, go to Settings → Connected Apps → Box → Disconnect → Connect again. "
                            "Changes can take a few minutes to propagate."
                        )
                    else:
                        raise Exception(f"Box upload failed due to insufficient permissions (403): {upload_err}")
                else:
                    raise Exception(f"Failed to upload PDF to Box: {upload_err}")
            except Exception as upload_err:
                raise Exception(f"Failed to upload PDF to Box: {upload_err}")
        else:
            print(f"[Box] PDF not found or empty at {pdf_path}, skipping PDF upload")

        # Upload transcript (named script)
        if transcript_path.exists() and transcript_path.stat().st_size > 0:
            file_size = transcript_path.stat().st_size
            transcript_filename = f"{safe_meeting_name}_named_script.txt"
            print(f"[Box] Uploading transcript: {transcript_filename} ({file_size} bytes)")
            try:
                existing_files = list(current_folder.get_items())
                existing_file_id = None
                for item in existing_files:
                    if item.type == 'file' and item.name == transcript_filename:
                        existing_file_id = item.id
                        break
                if existing_file_id:
                    with open(transcript_path, 'rb') as f:
                        file = client.file(existing_file_id).update_contents_with_stream(
                            f,
                            etag=None
                        )
                    upload_results["transcript"] = {"id": file.id, "name": transcript_filename}
                    print(f"[Box] Uploaded transcript (new version): {meeting_folder_path}/{transcript_filename} (ID: {file.id}, {file_size} bytes)")
                else:
                    with open(transcript_path, 'rb') as f:
                        file = current_folder.upload_stream(f, transcript_filename)
                    if file and file.id:
                        upload_results["transcript"] = {"id": file.id, "name": transcript_filename}
                        print(f"[Box] Uploaded transcript: {meeting_folder_path}/{transcript_filename} (ID: {file.id}, {file_size} bytes)")
                    else:
                        raise Exception("Box upload completed but file object is invalid")
            except BoxAPIException as upload_err:
                if upload_err.status == 403:
                    error_msg = str(upload_err)
                    if "insufficient_scope" in error_msg.lower():
                        users = read_users()
                        if user_email.lower() in users:
                            if "connected_apps" not in users[user_email.lower()]:
                                users[user_email.lower()]["connected_apps"] = {}
                            if "box" not in users[user_email.lower()]["connected_apps"]:
                                users[user_email.lower()]["connected_apps"]["box"] = {}
                            users[user_email.lower()]["connected_apps"]["box"]["needs_scope_update"] = True
                            write_users(users)
                        raise BoxInsufficientScopeError(
                            "Box upload failed: Your Box access token doesn't have write permissions. "
                            "Developer action: In Box Developer Console → My Apps → Your App → Configuration → Application Scopes, "
                            "enable 'Read and write all files and folders stored in Box'. "
                            "User action: After enabling scopes, go to Settings → Connected Apps → Box → Disconnect → Connect again. "
                            "Changes can take a few minutes to propagate."
                        )
                    else:
                        raise Exception(f"Box upload failed due to insufficient permissions (403): {upload_err}")
                else:
                    raise Exception(f"Failed to upload transcript to Box: {upload_err}")
            except Exception as upload_err:
                raise Exception(f"Failed to upload transcript to Box: {upload_err}")
        else:
            print(f"[Box] Transcript not found or empty at {transcript_path}, skipping transcript upload")
        
        print(f"[Box] Upload completed successfully")
        return upload_results
        
    except BoxInsufficientScopeError:
        # Re-raise scope errors as-is
        raise
    except BoxTokenError:
        # Re-raise token errors as-is
        raise
    except BoxAPIException as e:
        error_msg = str(e)
        if e.status == 401:
            # Token expired - this shouldn't happen if refresh_if_needed worked, but handle it
            raise BoxTokenError(
                "Box token expired during upload. "
                "If this persists, go to Settings → Connected Apps → Box → Disconnect → Connect again."
            )
        else:
            raise Exception(f"Box API error: {error_msg}")
    except Exception as e:
        print(f"[Box] Upload error: {e}")
        import traceback
        if os.getenv("FLASK_DEBUG") == "1":
            traceback.print_exc()
        raise

def run_pipeline(audio_path: Path, cfg: dict, participants: list = None):
    """Run the transcription pipeline with optional participant list for email sending."""
    PY = sys.executable
    stem = audio_path.stem
    meeting_name = stem  # Default to stem

    # Optional meeting-processing job tracking (for UI progress page)
    track_job = bool(cfg.get("track_meeting_job"))
    job_meeting_id = (cfg.get("meeting_id") or stem)
    job_meeting_name = (cfg.get("meeting_name") or meeting_name)

    def _job_update(**kwargs):
        if not track_job:
            return
        try:
            _upsert_meeting_job(job_meeting_id, meeting_name=job_meeting_name, **kwargs)
        except Exception:
            pass

    def _job_log(msg: str):
        if not track_job:
            return
        try:
            _append_meeting_job_log(job_meeting_id, msg)
        except Exception:
            pass

    def _run_and_stream(cmd: list[str], *, stage: str, p_start: int, p_end: int, env: dict, target_seconds: float = 600.0) -> int:
        """
        Run a subprocess while streaming stdout/stderr to both server logs and the meeting job log.
        Progress is stage-based with a best-effort ramp during execution (capped at p_end-1 until complete).
        """
        _job_update(status="processing", stage=stage, percent=p_start)
        _job_log(f"[{datetime.now().isoformat()}] ▶ {stage}: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as e:
            _job_log(f"[{datetime.now().isoformat()}] ❌ Failed to start: {e}")
            _job_update(status="failed", stage=stage, percent=p_start, error=str(e))
            return 1

        start_ts = time.time()
        last_update = 0.0
        current_percent = p_start
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = (line or "").rstrip("\n")
                    if line:
                        # Parse transcription progress lines emitted by transcribe.py
                        if stage == "transcription" and line.startswith("TRANSCRIBE_PROGRESS "):
                            try:
                                m = re.search(r"percent=(\\d+).*done=([0-9.]+).*total=([0-9.]+)", line)
                                if m:
                                    pct = int(m.group(1))
                                    done_s = float(m.group(2))
                                    total_s = float(m.group(3))
                                    _job_update(transcription_percent=pct, transcribed_seconds=done_s, total_audio_seconds=total_s)
                            except Exception:
                                pass
                        print(line)
                        _job_log(line)
                    now_ts = time.time()
                    if now_ts - last_update >= 1.0:
                        elapsed = now_ts - start_ts
                        span = max(1, int(p_end - p_start))
                        ramp = int(min(span - 1, (elapsed / max(1.0, target_seconds)) * span))
                        current_percent = max(current_percent, min(p_end - 1, p_start + ramp))
                        _job_update(stage=stage, percent=current_percent)
                        last_update = now_ts
        finally:
            rc = proc.wait()

        if rc != 0:
            _job_log(f"[{datetime.now().isoformat()}] ❌ {stage} failed (exit {rc})")
            _job_update(status="failed", stage=stage, percent=current_percent, error=f"Command failed: {' '.join(cmd)}")
            return rc

        _job_update(stage=stage, percent=p_end)
        _job_log(f"[{datetime.now().isoformat()}] ✅ {stage} complete")
        return 0
    
    # Try to extract meeting name from filename (format: name_TIMESTAMP)
    if "_" in stem:
        parts = stem.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():  # Has timestamp suffix
            meeting_name = parts[0].replace("_", " ")

    # Calculate optimal speaker count from participants (with buffer for unknowns)
    # This helps AssemblyAI's diarization accuracy significantly
    speakers_expected = cfg.get("speakers_expected")
    if speakers_expected is None and participants:
        # Count enrolled participants and add buffer for unknown speakers
        enrolled_count = len(participants)
        # Add 2-3 buffer for potential unknown speakers (common in meetings)
        speakers_expected = enrolled_count + 2
        print(f"Auto-calculated speaker count: {enrolled_count} enrolled + 2 buffer = {speakers_expected} total")
    
    # Get user email for vocabulary (meeting owner)
    # Use first participant as meeting owner, or current session user
    user_email = None
    if participants and len(participants) > 0:
        first_participant = participants[0]
        if isinstance(first_participant, dict):
            user_email = first_participant.get("email", "").lower()
        elif isinstance(first_participant, str):
            user_email = first_participant.lower()
    
    # If no participants, try to get from current session
    if not user_email:
        current = current_user()
        if current:
            user_email = current["email"].lower()
    
    cmd1 = [PY, "transcribe.py", str(audio_path)]
    if speakers_expected is not None:
        cmd1 += ["--speakers", str(speakers_expected)]
    
    # Build participant names list for filtering enrollment files
    # Pass both username and firstname,lastname formats for maximum compatibility
    participant_names = []
    participant_username_to_email = {}  # Map username -> email for later email/upload
    if participants:
        users = read_users()
        for p in participants:
            email = None
            if isinstance(p, str):
                email = p.lower()
            elif isinstance(p, dict) and "email" in p:
                email = p["email"].lower()
            
            if email and email in users:
                user = users[email]
                username = user.get("username", "").strip().lower()
                first = user.get("first", "").strip().lower()
                last = user.get("last", "").strip().lower()
                
                # Add username format (primary - enrollment files are named by username)
                if username:
                    participant_names.append(username)
                    participant_username_to_email[username] = email
                
                # Also add firstname,lastname format (for backward compatibility)
                if first and last:
                    name_format = f"{first},{last}"
                    if name_format not in participant_names:
                        participant_names.append(name_format)
                    # Map this format to email too
                    participant_username_to_email[name_format] = email
    
    cmd2 = [PY, "identify_speakers.py", str(OUTPUT_DIR / f"{stem}_utterances.json"), str(ENROLL_DIR), str(OUTPUT_DIR / f"{stem}_named_script.txt")]
    if participant_names:
        # Pass participant names as comma-separated list (usernames and firstname,lastname)
        cmd2 += ["--participants", ",".join(participant_names)]

    # Prepare environment with user email for vocabulary
    import os
    env = os.environ.copy()
    # Load .env file manually (same as run_cmd does)
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            with open(env_file, 'r', encoding='utf-8-sig') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        env[key] = value
        except Exception as e:
            print(f"Warning: Could not load .env file: {e}")
    
    # Add user email to environment for vocabulary loading
    if user_email:
        env["VOCABULARY_USER_EMAIL"] = user_email
        print(f"Using custom vocabulary for user: {user_email}")

    # Enable chunked Whisper transcription so we can report "% of meeting transcribed" progress.
    # (Keep this configurable: users can override via env var.)
    env.setdefault("WHISPER_CHUNK_SECONDS", "20")
    
    # Run transcription (required)
    _job_update(status="processing", stage="transcription", percent=10)
    _job_log(f"[{datetime.now().isoformat()}] Starting transcription for {audio_path.name}")
    rc1 = _run_and_stream(cmd1, stage="transcription", p_start=10, p_end=60, env=env, target_seconds=900.0)
    if rc1 != 0:
        print(f"\n❌ Pipeline stopped (exit {rc1})")
        return

    # Run speaker identification (optional – meeting should still show up even if this fails)
    speaker_id_ok = True
    try:
        _job_update(status="processing", stage="speaker_identification", percent=75)
        _job_log(f"[{datetime.now().isoformat()}] Starting speaker identification (optional)")
        rc2 = _run_and_stream(cmd2, stage="speaker_identification", p_start=75, p_end=85, env=env, target_seconds=240.0)
        if rc2 != 0:
            speaker_id_ok = False
    except Exception as e:
        speaker_id_ok = False
        print(f"Error running command {' '.join(cmd2)}: {e}")
    else:
        # _run_and_stream handles logging and return code
        pass

    if not speaker_id_ok:
        # Fallback: if we have the diarized transcript, copy it into the "named" transcript path
        # so the rest of the pipeline (PDF + meetings index) can still proceed.
        try:
            raw_transcript_path = OUTPUT_DIR / f"{stem}_script.txt"
            fallback_named_path = OUTPUT_DIR / f"{stem}_named_script.txt"
            if raw_transcript_path.exists() and not fallback_named_path.exists():
                fallback_named_path.write_text(raw_transcript_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                print(f"⚠️  Speaker ID failed; using fallback transcript at {fallback_named_path.name}")
        except Exception as e:
            print(f"⚠️  Speaker ID failed and fallback transcript could not be created: {e}")

    # Create comprehensive meeting report PDF using meeting_pdf_summarizer
    # This uses Ollama AI to generate structured summaries from the named transcript
    pdf_path = OUTPUT_DIR / f"{stem}_meeting_report.pdf"
    pdf_exists = False
    json_path = OUTPUT_DIR / f"{stem}_named_script.json"
    transcript_path = OUTPUT_DIR / f"{stem}_named_script.txt"
    utterances_path = OUTPUT_DIR / f"{stem}_utterances.json"
    
    # Use meeting_pdf_summarizer to create AI-powered meeting report
    if transcript_path.exists():
        try:
            _job_update(status="processing", stage="generating_pdfs", percent=85, error=None)
            _job_log(f"[{datetime.now().isoformat()}] Generating PDFs…")
            # Run meeting_pdf_summarizer/main.py to create the comprehensive meeting report
            # It takes the named transcript and uses Ollama to generate structured summaries
            summarizer_main = ROOT / "meeting_pdf_summarizer" / "main.py"
            roles_json = ROOT / "meeting_pdf_summarizer" / "roles.json"
            
            if not summarizer_main.exists():
                print(f"⚠️  Warning: meeting_pdf_summarizer/main.py not found at {summarizer_main}")
                pdf_exists = False
            else:
                # Get upload date from config (set when file was uploaded) or file mtime as fallback
                upload_date = None
                if cfg.get("upload_timestamp"):
                    upload_date = cfg["upload_timestamp"]
                elif audio_path.exists():
                    # Fallback: use file modification time
                    upload_date = datetime.fromtimestamp(audio_path.stat().st_mtime).isoformat()
                else:
                    # Last resort: use current time
                    upload_date = datetime.now().isoformat()
                
                # Get source organizations from config (set during participant selection)
                source_orgs = cfg.get("source_organizations", [])
                source_orgs_str = ",".join(source_orgs) if source_orgs else ""
                
                cmd3 = [PY, str(summarizer_main),
                       "--input", str(transcript_path),
                       "--output", str(pdf_path),
                       "--roles", str(roles_json)]
                
                # Add upload date and source organizations if available
                if upload_date:
                    cmd3.extend(["--upload-date", upload_date])
                if source_orgs_str:
                    cmd3.extend(["--source-organizations", source_orgs_str])
                
                print(f"🔧 Running meeting_pdf_summarizer to create AI-powered meeting report...")
                print(f"   Input transcript: {transcript_path}")
                print(f"   Output PDF: {pdf_path}")
                print(f"   Roles JSON: {roles_json}")
                if upload_date:
                    print(f"   Upload date: {upload_date}")
                if source_orgs:
                    print(f"   Source organizations: {', '.join(source_orgs)}")
                print(f"   Command: {' '.join(cmd3)}")
                rc = run_cmd(cmd3)
                
                if rc == 0 and pdf_path.exists():
                    # Verify PDF is not empty
                    if pdf_path.stat().st_size > 0:
                        pdf_exists = True
                        print(f"✅ Created AI-powered meeting report PDF: {pdf_path} ({pdf_path.stat().st_size} bytes)")
                    else:
                        print(f"❌ ERROR: Meeting report PDF was created but is empty (0 bytes)")
                        print(f"   This indicates a problem with the PDF generation process.")
                        pdf_exists = False
                else:
                    print(f"❌ ERROR: Meeting report PDF creation FAILED!")
                    print(f"   Exit code: {rc}")
                    print(f"   PDF path: {pdf_path}")
                    print(f"   PDF exists: {pdf_path.exists()}")
                    print(f"   Command: {' '.join(cmd3)}")
                    print(f"   ")
                    print(f"   ⚠️  CRITICAL: Make sure Ollama is running!")
                    print(f"   - Check: ollama list (should show your models)")
                    print(f"   - Install model: ollama pull llama3.1:8b")
                    print(f"   - Start Ollama if not running")
                    print(f"   ")
                    print(f"   Without the PDF, only the transcript will be emailed/uploaded.")
                    pdf_exists = False
        except Exception as e:
            print(f"⚠️  Warning: Could not create meeting report PDF: {e}")
            pdf_exists = False
    
    # Create summarized version of PDF for sending/sharing (important info only)
    summary_pdf_path = None
    if pdf_exists and pdf_path.exists() and pdf_path.stat().st_size > 0:
        try:
            from meeting_pdf_summarizer import prepare_pdf_for_sending
            print(f"\n📄 Creating summarized version of meeting report for sharing...")
            print(f"   Original PDF: {pdf_path.name} ({pdf_path.stat().st_size} bytes)")
            summary_pdf_path = prepare_pdf_for_sending(pdf_path, output_dir=OUTPUT_DIR)
            if summary_pdf_path and summary_pdf_path.exists() and summary_pdf_path.stat().st_size > 0:
                summary_size = summary_pdf_path.stat().st_size
                print(f"✅ Created summary PDF: {summary_pdf_path.name} ({summary_size} bytes)")
                print(f"   Summary is {summary_size / pdf_path.stat().st_size * 100:.1f}% of original size")
                # Use summary PDF for sending/uploading instead of full report
                pdf_path = summary_pdf_path
            else:
                print(f"⚠️  Summary PDF creation failed or returned empty file")
                print(f"   Using full meeting report PDF for sending/uploading")
                summary_pdf_path = None
        except ImportError as e:
            print(f"⚠️  Could not import PDF summarizer: {e}")
            print(f"   Install dependencies: pip install pypdf")
            print(f"   Using full meeting report PDF")
        except Exception as e:
            print(f"⚠️  Error creating summary PDF: {e}")
            print(f"   Using full meeting report PDF")
            import traceback
            traceback.print_exc()
    
    # Generate simple transcript PDF (does NOT require Ollama)
    transcript_pdf_path = OUTPUT_DIR / f"{stem}_transcript.pdf"
    transcript_pdf_exists = False
    named_json_for_pdf = OUTPUT_DIR / f"{stem}_named_script.json"
    if named_json_for_pdf.exists():
        try:
            _job_log(f"[{datetime.now().isoformat()}] Generating transcript PDF...")
            from email_named_script import create_pdf as _create_transcript_pdf, read_db as _read_db_for_pdf
            people_for_pdf = {}
            try:
                people_for_pdf = _read_db_for_pdf(Path("input") / "emails.csv")
            except Exception:
                pass
            ok = _create_transcript_pdf(named_json_for_pdf, people_for_pdf, transcript_pdf_path)
            if ok and transcript_pdf_path.exists() and transcript_pdf_path.stat().st_size > 0:
                transcript_pdf_exists = True
                print(f"✅ Created transcript PDF: {transcript_pdf_path} ({transcript_pdf_path.stat().st_size} bytes)")
            else:
                print(f"⚠️  Transcript PDF generation returned False or file is empty")
        except Exception as e:
            print(f"⚠️  Could not create transcript PDF: {e}")
    else:
        # Fallback: create named_script.json from utterances.json if it doesn't exist
        utterances_json = OUTPUT_DIR / f"{stem}_utterances.json"
        if utterances_json.exists():
            try:
                _job_log(f"[{datetime.now().isoformat()}] Creating named_script.json from utterances...")
                utterances_data = json.loads(utterances_json.read_text(encoding="utf-8"))
                named_data = []
                for u in utterances_data:
                    named_data.append({
                        'speaker_name': u.get('speaker', 'Unknown'),
                        'text': u.get('text', '')
                    })
                named_json_for_pdf.write_text(json.dumps(named_data, indent=2), encoding="utf-8")
                print(f"✅ Created {named_json_for_pdf.name} from utterances")
                
                # Now generate PDF
                from email_named_script import create_pdf as _create_transcript_pdf, read_db as _read_db_for_pdf
                people_for_pdf = {}
                try:
                    people_for_pdf = _read_db_for_pdf(Path("input") / "emails.csv")
                except Exception:
                    pass
                ok = _create_transcript_pdf(named_json_for_pdf, people_for_pdf, transcript_pdf_path)
                if ok and transcript_pdf_path.exists() and transcript_pdf_path.stat().st_size > 0:
                    transcript_pdf_exists = True
                    print(f"✅ Created transcript PDF: {transcript_pdf_path} ({transcript_pdf_path.stat().st_size} bytes)")
            except Exception as e:
                print(f"⚠️  Could not create transcript PDF from utterances: {e}")
    
    # Save meeting metadata after successful processing
    try:
        audio_size = audio_path.stat().st_size if audio_path.exists() else 0
        transcript_path = OUTPUT_DIR / f"{stem}_named_script.txt"
        transcript_exists = transcript_path.exists()
        
        # Get speakers who were identified from JSON (for labeling only)
        # Also track which emails correspond to identified speakers for email/upload
        speakers = set()
        identified_speaker_emails = set()  # Emails of people who actually spoke (based on username matching)
        json_path = OUTPUT_DIR / f"{stem}_named_script.json"
        if json_path.exists():
            try:
                json_data = json.loads(json_path.read_text(encoding="utf-8"))
                users_lookup = read_users()
                for r in json_data:
                    speaker_name = r.get("speaker_name", "").strip()
                    if speaker_name and speaker_name != "Unknown":
                        # Remove any (2), (3) etc. patterns first
                        speaker_name_clean = re.sub(r"\(\d+\)", "", speaker_name).strip().lower()
                        
                        # Map speaker name (username or firstname,lastname) to email
                        speaker_email = None
                        display_name = None
                        
                        # First check if it's a username (no comma)
                        if "," not in speaker_name_clean:
                            # Look up by username
                            for email, user in users_lookup.items():
                                username = user.get("username", "").strip().lower()
                                if username == speaker_name_clean:
                                    speaker_email = email.lower()
                                    display_name = f"{user['first']} {user['last']}"
                                    speakers.add(display_name)
                                    identified_speaker_emails.add(speaker_email)
                                    break
                        
                        # If not found by username, check firstname,lastname format
                        if not speaker_email and "," in speaker_name_clean:
                            parts = speaker_name_clean.split(",")
                            if len(parts) == 2:
                                first = parts[0].strip()
                                last = parts[1].strip()
                                for email, user in users_lookup.items():
                                    user_first = user.get("first", "").strip().lower()
                                    user_last = user.get("last", "").strip().lower()
                                    if user_first == first and user_last == last:
                                        speaker_email = email.lower()
                                        display_name = f"{user['first']} {user['last']}"
                                        speakers.add(display_name)
                                        identified_speaker_emails.add(speaker_email)
                                        break
                        
                        # Fallback: just use the name as-is for display
                        if not display_name:
                            if "," in speaker_name_clean:
                                parts = speaker_name_clean.split(",")
                                if len(parts) == 2:
                                    first = parts[0].strip().capitalize()
                                    last = parts[1].strip().capitalize()
                                    speakers.add(f"{first} {last}")
                                else:
                                    speakers.add(speaker_name_clean.capitalize())
                            else:
                                speakers.add(speaker_name_clean.capitalize())
            except Exception as e:
                print(f"Warning: Could not parse speaker JSON: {e}")
        
        # Determine who to email/upload to:
        # 1. People who actually spoke (identified by voice matching to usernames)
        # 2. All participants (if they were specified)
        # 3. Uploader (if different from participants)
        
        # Start with identified speakers (people who actually spoke)
        participant_emails = list(identified_speaker_emails)
        
        # Add all specified participants (they should get the transcript even if they didn't speak)
        if participants:
            for p in participants:
                email = None
                if isinstance(p, str):
                    email = p.lower()
                elif isinstance(p, dict) and "email" in p:
                    email = p["email"].lower()
                if email and email not in participant_emails:
                    participant_emails.append(email)
        
        # Add uploader if not already in list
        uploader_email = cfg.get("uploader_email")
        if uploader_email and uploader_email.lower() not in participant_emails:
            participant_emails.append(uploader_email.lower())

        def _safe_relpath(p: Path | None) -> str | None:
            if not p:
                return None
            try:
                # Prefer a clean path relative to repo root
                return str(p.resolve().relative_to(ROOT))
            except Exception:
                # Fallback: keep whatever we have (relative or absolute)
                return str(p)
        
        meeting_data = {
            "id": stem,
            "name": meeting_name,
            "original_filename": audio_path.name,
            "processed_at": datetime.now().isoformat(),
            "audio_path": _safe_relpath(audio_path) if audio_path.exists() else None,
            "transcript_path": _safe_relpath(transcript_path) if transcript_exists else None,
            "transcript_pdf_path": _safe_relpath(transcript_pdf_path) if transcript_pdf_exists else None,
            "pdf_path": _safe_relpath(pdf_path) if pdf_exists else None,
            "audio_size_bytes": audio_size,
            "speakers": sorted(list(speakers)),  # For labeling in transcript
            "speaker_count": len(speakers),
            "participants": participant_emails,  # For email/account access
        }
        save_meeting(meeting_data)
        print(f"\n📝 Meeting metadata saved: {meeting_name}")
        
        # Send emails to participants
        if participant_emails:
            users = read_users()
            script_text = transcript_path.read_text(encoding="utf-8", errors="replace") if transcript_exists else ""
            
            for participant_email in participant_emails:
                user = users.get(participant_email.lower())
                if not user:
                    continue
                
                # Check if user wants to receive meeting emails
                receive_emails = user.get("receive_meeting_emails", True)
                if not receive_emails:
                    print(f"📧 Skipping email to {participant_email} (user opted out)")
                    continue
                
                # ONLY SEND EMAIL IF PDF EXISTS - PDFs ONLY, NO TXT FILES
                if not pdf_exists or not pdf_path.exists():
                    if not pdf_exists:
                        print(f"  ⚠️  Skipping email to {participant_email} - PDF was not created (pdf_exists=False)")
                    elif not pdf_path.exists():
                        print(f"  ⚠️  Skipping email to {participant_email} - PDF file does not exist at {pdf_path}")
                    continue
                
                # Prepare PDF attachment - PDFs ONLY
                attachments = []
                print(f"  📎 Preparing email attachments for {participant_email}...")
                print(f"     pdf_exists flag: {pdf_exists}")
                print(f"     pdf_path: {pdf_path}")
                print(f"     pdf_path.exists(): {pdf_path.exists()}")
                
                try:
                    with open(pdf_path, 'rb') as f:
                        pdf_content = f.read()
                        if len(pdf_content) > 0:
                            attachments.append({
                                "content": pdf_content,
                                "maintype": "application",
                                "subtype": "pdf",
                                "filename": f"{meeting_name}_meeting_report.pdf"
                            })
                            print(f"  ✓ Attached meeting report PDF ({len(pdf_content)} bytes) to email for {participant_email}")
                        else:
                            print(f"  ⚠️  Warning: PDF file is empty, skipping email to {participant_email}")
                            continue
                except Exception as e:
                    print(f"  ⚠️  Warning: Could not attach meeting report PDF for {participant_email}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
                
                # Format date with dashes for display
                processed_date = meeting_data.get("processed_at", "")
                if processed_date:
                    try:
                        # Parse ISO format and format with dashes
                        date_obj = datetime.fromisoformat(processed_date.replace('Z', '+00:00'))
                        formatted_date = date_obj.strftime("%Y-%m-%d")
                    except:
                        formatted_date = processed_date[:10] if len(processed_date) >= 10 else processed_date
                else:
                    formatted_date = "N/A"
                
                # Send email with meeting report PDF ONLY
                subject = f"{meeting_name} meeting report - {formatted_date}"
                body = f"""Hi {user['first']},

Here's the meeting report from the {meeting_name} meeting (Date: {formatted_date}).

The report includes:
• Executive summary and key insights
• Participation metrics
• Action items and decisions
• Full transcript with speaker identification

Best,
Phi AI Team"""
                
                send_email(participant_email, subject, body, attachments)
                print(f"📧 Sent meeting report PDF to {participant_email}")
        
        # Upload to connected apps for all participants (enqueue background job)
        if (pdf_path.exists() and pdf_path.stat().st_size > 0) or (transcript_path.exists() and transcript_path.stat().st_size > 0):
            try:
                participant_emails_list = []
                if participants:
                    for p in participants:
                        if isinstance(p, str):
                            participant_emails_list.append(p.lower())
                        elif isinstance(p, dict) and "email" in p:
                            participant_emails_list.append(p["email"].lower())

                uploader_email = cfg.get("uploader_email")
                if uploader_email and uploader_email.lower() not in participant_emails_list:
                    participant_emails_list.append(uploader_email.lower())

                print(f"📤 Enqueuing upload job for meeting files...")
                print(f"   PDF path: {pdf_path}")
                print(f"   PDF exists: {pdf_path.exists()}")
                print(f"   Transcript path: {transcript_path}")
                print(f"   Transcript exists: {transcript_path.exists()}")
                enqueue_upload_job(meeting_name, pdf_path, transcript_path, participant_emails_list)
            except Exception as e:
                print(f"⚠️  Warning: Could not enqueue upload job: {e}")
                import traceback
                traceback.print_exc()
        
    except Exception as e:
        print(f"\n⚠️  Warning: Could not save meeting metadata: {e}")

    _job_update(status="done", stage="done", percent=100, error=None)
    _job_log(f"[{datetime.now().isoformat()}] 🎉 Done")
    print(f"\n✅ Completed pipeline for: {audio_path.name}\n")

# Initialize
ensure_dirs()
init_users_csv()

# Startup validation for cloud storage credentials
def validate_cloud_storage_credentials():
    """Validate that required credentials are available if any users have connected apps"""
    try:
        users = read_users()
        has_dropbox_connection = False
        has_box_connection = False
        
        for user_email, user_data in users.items():
            connected_apps = user_data.get("connected_apps", {})
            if "dropbox" in connected_apps:
                has_dropbox_connection = True
            if "box" in connected_apps:
                has_box_connection = True
        
        # Check Dropbox credentials if any user has Dropbox connected
        if has_dropbox_connection:
            dropbox_key = os.getenv("DROPBOX_CLIENT_ID") or os.getenv("DROPBOX_APP_KEY")
            dropbox_secret = os.getenv("DROPBOX_CLIENT_SECRET") or os.getenv("DROPBOX_APP_SECRET")
            if not dropbox_key or not dropbox_secret:
                print("[WARN] ⚠️  Dropbox integration is enabled but credentials are missing!")
                print("       Developer action required: set DROPBOX_CLIENT_ID and DROPBOX_CLIENT_SECRET")
                print("       (or DROPBOX_APP_KEY and DROPBOX_APP_SECRET) in the server .env and restart.")
            else:
                print("[INFO] ✓ Dropbox credentials configured")
        
        # Check Box credentials and SDK if any user has Box connected
        if has_box_connection:
            box_key = os.getenv("BOX_CLIENT_ID")
            box_secret = os.getenv("BOX_CLIENT_SECRET")
            if not box_key or not box_secret:
                print("[WARN] ⚠️  Box integration is enabled but credentials are missing!")
                print("       Developer action required: set BOX_CLIENT_ID and BOX_CLIENT_SECRET")
                print("       in the server .env and restart.")
            else:
                print("[INFO] ✓ Box credentials configured")
            
            # Check if boxsdk is installed
            try:
                import boxsdk  # type: ignore
                print("[INFO] ✓ Box SDK (boxsdk) is installed")
            except ImportError:
                print("[WARN] ⚠️  Box SDK (boxsdk) is not installed!")
                print("       Developer action required: pip install boxsdk")
                print("       Ensure requirements.txt includes boxsdk and run: pip install -r requirements.txt")
    except Exception as e:
        print(f"[WARN] Could not validate cloud storage credentials: {e}")

# Run validation on startup
validate_cloud_storage_credentials()
start_upload_worker()

# ----------------------------
# Custom Vocabulary storage
# ----------------------------
def load_vocabulary() -> dict:
    """Load vocabulary: {user_email: [vocab_entries]}"""
    if not VOCABULARY_JSON.exists():
        return {}
    try:
        return json.loads(VOCABULARY_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_vocabulary(vocab_dict: dict):
    """Save vocabulary"""
    VOCABULARY_JSON.write_text(json.dumps(vocab_dict, indent=2), encoding="utf-8")

def get_user_vocabulary(user_email: str) -> list:
    """Get all vocabulary entries for a user"""
    vocab_dict = load_vocabulary()
    return vocab_dict.get(user_email.lower(), [])

def save_user_vocabulary(user_email: str, entries: list):
    """Save vocabulary entries for a user"""
    vocab_dict = load_vocabulary()
    vocab_dict[user_email.lower()] = entries
    save_vocabulary(vocab_dict)

def get_user_custom_vocabulary(user_email: str) -> list[str]:
    """
    Get custom vocabulary terms as a list of strings for transcription pipeline.
    Returns just the terms (not full entries) for word boosting.
    """
    entries = get_user_vocabulary(user_email)
    terms = []
    for entry in entries:
        term = entry.get("term", "").strip()
        if term:
            terms.append(term)
        # Also include aliases
        aliases = entry.get("aliases", [])
        if isinstance(aliases, list):
            terms.extend([a.strip() for a in aliases if a.strip()])
        elif isinstance(aliases, str):
            # Handle comma-separated string
            terms.extend([a.strip() for a in aliases.split(",") if a.strip()])
    return terms

# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def index():
    if current_user():
        return redirect(url_for("account_home"))
    cfg = load_config()
    return render_template("index.html", watch_dir=cfg["watch_dir"], logged_in=bool(current_user()), user=current_user())

@app.get("/account")
def account_home():
    """Account homepage with dashboard"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    meetings = get_user_meetings(user["email"])
    
    # Check if user has enrollment audio (using firstname,lastname or username) - must be >= 30 seconds
    username = user.get("username", "").strip().lower()
    first = user.get("first", "").strip().lower()
    last = user.get("last", "").strip().lower()
    name_prefix = f"{first},{last}".lower() if first and last else ""
    has_enrollment = False
    if username:
        enroll_dir = ENROLL_DIR
        if enroll_dir.exists():
            for f in enroll_dir.iterdir():
                if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                    filename_lower = f.name.lower()
                    # Check both firstname,lastname format and username format (backward compatibility)
                    matches = False
                    if name_prefix and filename_lower.startswith(name_prefix):
                        matches = True
                    elif filename_lower.startswith(username.lower()):
                        matches = True
                    
                    if matches and f.stat().st_size > 0:
                        duration = get_audio_duration(f)
                        if duration >= 30.0:
                            has_enrollment = True
                            break
    
    return render_template("account_home.html", user=user, total_meetings=len(meetings), has_enrollment=has_enrollment)

@app.get("/account/meetings")
def account_meetings():
    """Past meetings page"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()

    # If a meeting is currently uploading/processing, show the processing page first
    # unless the user explicitly bypasses it (?show_list=1).
    show_list = request.args.get("show_list", "").strip() in ("1", "true", "yes")
    if not show_list:
        active_jobs = _list_active_meeting_jobs()
        if active_jobs:
            active_job = active_jobs[0]
            return render_template(
                "meeting_processing.html",
                user=user,
                job=active_job,
                meetings_url=url_for("account_meetings", show_list="1"),
            )

    meetings = get_user_meetings(user["email"])
    
    # Detect unknown speakers for each meeting
    for meeting in meetings:
        unknown_data = detect_unknown_speakers(meeting)
        meeting.update(unknown_data)
    
    return render_template("account_meetings.html", user=user, meetings=meetings)

def sync_user_organizations_from_orgs_json(user_email: str, users: dict):
    """Sync user's organizations from organizations.json to user record if missing."""
    orgs_json = load_organizations()
    user_email_lower = user_email.lower()
    
    if user_email_lower not in users:
        return []
    
    user_orgs = users[user_email_lower].get("organizations", [])
    user_org_names = {org.get("name") for org in user_orgs if org.get("name")}
    
    # Find organizations where user is a member
    for org_name, org_data in orgs_json.items():
        members = org_data.get("members", [])
        if user_email_lower in [m.lower() for m in members]:
            # User is a member but not in their organizations list
            if org_name not in user_org_names:
                org_type = org_data.get("type", "other")
                # Add to user's organizations (with a default role if missing)
                user_orgs.append({
                    "name": org_name,
                    "type": org_type,
                    "role": "Member"  # Default role
                })
                user_org_names.add(org_name)
    
    # Update user record if organizations were synced
    if user_orgs != users[user_email_lower].get("organizations", []):
        users[user_email_lower]["organizations"] = user_orgs
        write_users(users)
    
    return user_orgs

@app.get("/account/settings")
def account_get():
    """Account settings page"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    
    # Sync organizations from organizations.json if user record is missing them
    users = read_users()
    user_email = user["email"].lower()
    if not user.get("organizations") or len(user.get("organizations", [])) == 0:
        synced_orgs = sync_user_organizations_from_orgs_json(user_email, users)
        if synced_orgs:
            # Reload user with synced organizations
            users = read_users()
            user = users.get(user_email, user)
    
    organizations_directory = load_organizations_directory()
    # Update user's organization display names to include abbreviations
    if user.get("organizations"):
        for org in user["organizations"]:
            org_name = org.get("name", "")
            # Find matching org in directory to get abbreviation and address
            for dir_org in organizations_directory:
                if dir_org["name"] == org_name:
                    if dir_org.get("abbreviation"):
                        org["display_name"] = f"{org_name} ({dir_org['abbreviation']})"
                    else:
                        org["display_name"] = org_name
                    if dir_org.get("address"):
                        org["address"] = dir_org["address"]
                    break
            if "display_name" not in org:
                org["display_name"] = org_name
    return render_template("account.html", user=user, org_types=ORGANIZATION_TYPES, organizations_directory=organizations_directory)

@app.get("/account/edit_positions")
def edit_positions():
    """Edit positions page"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    organizations_directory = load_organizations_directory()
    return render_template("edit_positions.html", user=user, org_types=ORGANIZATION_TYPES, organizations_directory=organizations_directory)

@app.post("/account/edit_positions")
def edit_positions_post():
    """Save position changes"""
    if not require_login():
        return redirect(url_for("login_get"))
    
    user = current_user()
    if not user:
        return redirect(url_for("login_get"))
    
    users = read_users()
    old_email = user["email"]
    
    # Parse positions from form
    positions = []
    pos_count = int(request.form.get("position_count", "0") or "0")
    
    # Require at least one position (cannot be empty)
    valid_positions = []
    for i in range(min(pos_count, 10)):
        pos_name = (request.form.get(f"pos_name_{i}") or "").strip()
        pos_type = (request.form.get(f"pos_type_{i}") or "").strip()
        # Try both pos_role_{i} (singular) and pos_roles_{i}[] (array format)
        pos_role = (request.form.get(f"pos_role_{i}") or "").strip()
        if not pos_role:
            # Try array format (get first role if multiple)
            roles_list = request.form.getlist(f"pos_roles_{i}[]")
            if roles_list:
                pos_role = roles_list[0].strip()
        
        if pos_name and pos_type and pos_role:
            valid_positions.append({
                "name": pos_name,
                "type": pos_type,
                "role": pos_role
            })
    
    # Must have at least one position
    if not valid_positions:
        flash("You must have at least one position. Please add at least one position.")
        return redirect(url_for("edit_positions"))
    
    positions = valid_positions
    
    # Remove user from old organizations not in new list
    old_orgs = user.get("organizations", [])
    old_org_names = {org["name"] for org in old_orgs}
    new_org_names = {org["name"] for org in positions}
    for org_name in old_org_names - new_org_names:
        remove_user_from_organization(org_name, old_email)
    
    # Update organizations.json for each new organization
    for org in positions:
        add_user_to_organization(org["name"], org["type"], old_email)
    
    # Update user's organizations (preserve username, connected_apps, and receive_meeting_emails)
    # Always preserve username, connected_apps, and receive_meeting_emails from current user data
    preserved_username = user.get("username", "").strip().lower()
    preserved_connected_apps = user.get("connected_apps", {})
    preserved_receive_emails = user.get("receive_meeting_emails", True)
    
    users[old_email]["organizations"] = positions
    users[old_email]["username"] = preserved_username
    users[old_email]["connected_apps"] = preserved_connected_apps
    users[old_email]["receive_meeting_emails"] = preserved_receive_emails
    write_users(users)
    sync_emails_csv(users)
    
    flash("Positions updated successfully!")
    return redirect(url_for("account_get"))

@app.get("/signup")
def signup_get():
    organizations_directory = load_organizations_directory()
    return render_template(
        "signup.html",
        org_types=ORGANIZATION_TYPES,
        organizations_directory=organizations_directory,
        errors={},
        form_data={},
        org_entries=[],
    )

def validate_username(username: str) -> tuple[bool, str]:
    """Validate username: no spaces, no special chars, case-insensitive uniqueness."""
    username = username.strip().lower()
    if not username:
        return False, "Username is required."
    if " " in username:
        return False, "Username cannot contain spaces."
    if not username.replace("_", "").replace("-", "").isalnum():
        return False, "Username can only contain letters, numbers, underscores, and hyphens."
    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if len(username) > 30:
        return False, "Username must be 30 characters or less."
    return True, username

@app.post("/signup")
def signup_post():
    first = (request.form.get("first") or "").strip()
    last = (request.form.get("last") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    email2 = (request.form.get("email2") or "").strip().lower()
    pw = request.form.get("password") or ""
    pw2 = request.form.get("password2") or ""
    username = (request.form.get("username") or "").strip()

    organizations_directory = load_organizations_directory()

    # Preserve entered values on errors (do not echo passwords back)
    def _build_org_entries_from_request() -> tuple[int, list[dict]]:
        try:
            c = int(request.form.get("org_count", "0") or "0")
        except Exception:
            c = 0
        c = max(0, min(10, c))
        entries: list[dict] = []
        for i in range(c):
            org_name = (request.form.get(f"org_name_{i}") or "").strip()
            org_address = (request.form.get(f"org_address_{i}") or "").strip()
            org_type = (request.form.get(f"org_type_{i}") or "").strip()
            org_roles = request.form.getlist(f"org_roles_{i}[]")
            org_roles = [r.strip() for r in org_roles if r and r.strip()]
            entries.append({
                "name": org_name,
                "address": org_address,
                "type": org_type,
                "roles": org_roles,
            })
        return c, entries

    org_count, org_entries = _build_org_entries_from_request()
    form_data = {
        "first": first,
        "last": last,
        "email": email,
        "email2": email2,
        "username": username,
        "org_count": org_count,
    }
    errors: dict[str, str] = {}

    if not first:
        errors["first"] = "First name is required."
    if not last:
        errors["last"] = "Last name is required."
    if not valid_email(email):
        errors["email"] = "Please enter a valid email."
    if email != email2:
        errors["email2"] = "Emails must match."
    if pw != pw2:
        errors["password2"] = "Passwords must match."
    if len(pw) < 8:
        errors["password"] = "Password must be at least 8 characters."
    
    # Validate username
    valid, username_result = validate_username(username)
    if not valid:
        errors["username"] = username_result
    username = username_result

    users = read_users()
    if email in users:
        errors["email"] = "That email is already registered. Please log in instead."

    # Check username uniqueness (case-insensitive)
    for existing_user in users.values():
        if existing_user.get("username", "").lower() == username.lower():
            errors["username"] = "That username is already taken. Please choose another."
            break

    # Parse organizations from form - NOW REQUIRED
    organizations = []
    org_count = int(org_count or 0)
    
    # Require at least one organization
    if org_count == 0:
        errors["organizations"] = "You must add at least one organization."
    
    for i in range(min(org_count, 10)):
        org_name = (request.form.get(f"org_name_{i}") or "").strip()
        org_type = (request.form.get(f"org_type_{i}") or "").strip() or None
        # Get roles as a list (multiple roles per organization)
        org_roles = request.form.getlist(f"org_roles_{i}[]")
        org_roles = [r.strip() for r in org_roles if r.strip()]
        
        if org_name:  # Only org_name is required
            # Support both single role (backward compatibility) and multiple roles
            if org_roles:
                organizations.append({
                    "name": org_name,
                    "type": org_type,
                    "roles": org_roles  # Multiple roles
                })
            else:
                # Fallback to single role for backward compatibility
                organizations.append({
                    "name": org_name,
                    "type": org_type,
                    "role": None  # No role specified
                })
    
    if org_count > 0 and not organizations:
        errors["organizations"] = "You must select at least one organization."

    if errors:
        # Render in-place so the user’s entries are preserved (no redirect wipe)
        return render_template(
            "signup.html",
            org_types=ORGANIZATION_TYPES,
            organizations_directory=organizations_directory,
            errors=errors,
            form_data=form_data,
            org_entries=org_entries,
        ), 400

    users[email] = {
        "first": first,
        "last": last,
        "email": email,
        "password_hash": generate_password_hash(pw),
        "organizations": organizations,
        "username": username,
    }
    write_users(users)
    sync_emails_csv(users)

    # Now that the user is created, add them to organizations.json
    try:
        for org in organizations:
            org_name = (org.get("name") or "").strip()
            org_type = (org.get("type") or "").strip() or "other"
            if org_name:
                add_user_to_organization(org_name, org_type, email)
    except Exception:
        pass

    session["user_email"] = email
    session.permanent = True
    flash("Account created. Voice enrollment is recommended, but you can skip for now.")
    return redirect(url_for("account_home"))

@app.get("/add_organization")
def add_organization_get():
    """Page for adding a new organization"""
    # Allow access during signup (no login required if return_to is signup)
    return_to = request.args.get("return_to", "")
    if return_to and "/signup" not in return_to:
        if not require_login():
            return redirect(url_for("login_get"))
    
    return_to = request.args.get("return_to", url_for("account_get"))
    org_index = request.args.get("org_index", "0")
    organizations_directory = load_organizations_directory()
    
    return render_template("add_organization.html", 
                         org_types=ORGANIZATION_TYPES, 
                         return_to=return_to,
                         org_index=org_index,
                         organizations_directory=organizations_directory)

@app.post("/add_organization")
def add_organization_post():
    """Handle adding a new organization"""
    # Allow access during signup (no login required if return_to is signup)
    return_to = request.form.get("return_to", "")
    if return_to and "/signup" not in return_to:
        if not require_login():
            return redirect(url_for("login_get"))
    
    return_to = request.form.get("return_to", url_for("account_get"))
    org_index = request.form.get("org_index", "0")
    
    name = (request.form.get("org_name") or "").strip()
    abbreviation = (request.form.get("org_abbreviation") or "").strip()
    address_line = (request.form.get("org_address") or "").strip()
    city = (request.form.get("org_city") or "").strip()
    state = (request.form.get("org_state") or "").strip()
    zip_code = (request.form.get("org_zip") or "").strip()
    org_type = (request.form.get("org_type") or "").strip() or None
    # Get roles as a list (multiple roles per organization)
    org_roles = request.form.getlist("org_roles[]")
    org_roles = [r.strip() for r in org_roles if r.strip()]
    
    if not name or not address_line or not city or not state or not zip_code:
        flash("Organization name, address, city, state, and zip code are required.")
        return redirect(url_for("add_organization_get", return_to=return_to, org_index=org_index))
    
    if not org_roles:
        flash("At least one role is required.")
        return redirect(url_for("add_organization_get", return_to=return_to, org_index=org_index))
    
    # Combine address components
    address = f"{address_line}, {city}, {state} {zip_code}"
    
    # Add to directory (type is optional)
    add_organization_to_directory(name, abbreviation, address, org_type or "other")
    
    # Redirect back with parameters so the calling page can use them (pass first role for backward compatibility)
    return redirect(f"{return_to}?added_org={url_encode(name)}&org_index={org_index}&org_roles={url_encode(','.join(org_roles))}&org_type={url_encode(org_type or '')}")

@app.get("/login")
def login_get():
    return render_template("login.html")

@app.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    pw = request.form.get("password") or ""
    
    users = read_users()
    user = users.get(email)
    if not user or not check_password_hash(user["password_hash"], pw):
        flash("Invalid email or password.")
        return redirect(url_for("login_get"))
    
    session["user_email"] = email
    session.permanent = True
    
    # Check if user needs to connect apps after login (from callback redirect)
    if session.get('oauth_connect_dropbox_after_login'):
        session.pop('oauth_connect_dropbox_after_login', None)
        return redirect(url_for("connect_dropbox_confirm"))
    
    if session.get('oauth_connect_googledrive_after_login'):
        session.pop('oauth_connect_googledrive_after_login', None)
        return redirect(url_for("connect_googledrive_confirm"))
    
    if session.get('oauth_connect_box_after_login'):
        session.pop('oauth_connect_box_after_login', None)
        return redirect(url_for("connect_box_confirm"))
    
    # Check if user wanted to connect apps (from authorize redirect)
    if session.get('oauth_connect_dropbox'):
        session.pop('oauth_connect_dropbox', None)
        return redirect(url_for("dropbox_authorize"))
    
    if session.get('oauth_connect_googledrive'):
        session.pop('oauth_connect_googledrive', None)
        return redirect(url_for("googledrive_authorize"))
    
    if session.get('oauth_connect_box'):
        session.pop('oauth_connect_box', None)
        return redirect(url_for("box_authorize"))
    
    return redirect(url_for("account_home"))

@app.get("/logout")
def logout():
    session.pop("user_email", None)
    flash("Logged out.")
    return redirect(url_for("index"))

@app.get("/forgot_password")
def forgot_password_get():
    return render_template("forgot_password.html")

@app.post("/forgot_password")
def forgot_password_post():
    email = (request.form.get("email") or "").strip().lower()
    users = read_users()
    
    if email not in users:
        # Don't reveal if email exists
        flash("If that email exists, a password reset link has been sent.")
        return redirect(url_for("forgot_password_get"))
    
    token = create_reset_token(email)
    reset_url = request.url_root.rstrip("/") + url_for("reset_password_get", token=token, email=email)
    
    body = f"""Hi {users[email]['first']},

You requested to reset your password. Click the link below:

{reset_url}

This link expires in 1 hour.

If you didn't request this, you can ignore this email.

Best,
Phi AI Team"""
    
    send_email(email, "Phi AI Password Reset", body)
    flash("If that email exists, a password reset link has been sent.")
    return redirect(url_for("forgot_password_get"))

@app.get("/reset_password")
def reset_password_get():
    token = request.args.get("token", "")
    email = request.args.get("email", "").strip().lower()
    
    if not token or not email:
        flash("Invalid reset link.")
        return redirect(url_for("login_get"))
    
    if not verify_reset_token(email, token):
        flash("Invalid or expired reset link.")
        return redirect(url_for("login_get"))
    
    return render_template("reset_password.html", token=token, email=email)

@app.post("/reset_password")
def reset_password_post():
    token = request.form.get("token", "")
    email = (request.form.get("email") or "").strip().lower()
    pw = request.form.get("password") or ""
    pw2 = request.form.get("password2") or ""
    
    if not verify_reset_token(email, token):
        flash("Invalid or expired reset link.")
        return redirect(url_for("login_get"))
    
    if pw != pw2 or len(pw) < 8:
        flash("Passwords must match and be at least 8 characters.")
        return redirect(url_for("reset_password_get", token=token, email=email))
    
    users = read_users()
    if email not in users:
        flash("User not found.")
        return redirect(url_for("login_get"))
    
    users[email]["password_hash"] = generate_password_hash(pw)
    write_users(users)
    
    # Remove token
    tokens = load_reset_tokens()
    tokens.pop(email.lower(), None)
    save_reset_tokens(tokens)
    
    flash("Password reset successfully. Please login.")
    return redirect(url_for("login_get"))

@app.get("/set_username")
def set_username_get():
    """Page to set username (required before enrollment)"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template("set_username.html", user=user)

@app.post("/set_username")
def set_username_post():
    """Handle username setting"""
    if not require_login():
        return redirect(url_for("login_get"))
    
    user = current_user()
    username = (request.form.get("username") or "").strip()
    
    # Validate username
    valid, username_result = validate_username(username)
    if not valid:
        flash(username_result)
        return redirect(url_for("set_username_get"))
    username = username_result
    
    # Check uniqueness
    users = read_users()
    for existing_email, existing_user in users.items():
        if existing_email != user["email"] and existing_user.get("username", "").lower() == username.lower():
            flash("That username is already taken. Please choose another.")
            return redirect(url_for("set_username_get"))
    
    # Update username
    users[user["email"]]["username"] = username
    write_users(users)
    
    flash("Username set successfully!")
    return redirect(url_for("enroll_get"))

@app.get("/enroll")
def enroll_get():
    """Voice enrollment page"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    
    # Get username - if missing, prompt user to set it
    username = user.get("username", "").strip().lower()
    if not username:
        # User needs to set username before enrolling
        flash("Please set your username first. You'll be redirected to set it.")
        return redirect(url_for("set_username_get"))
    
    enroll_dir = ENROLL_DIR
    recordings = []
    
    if enroll_dir.exists():
        for f in sorted(enroll_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                # Only include files that match the current user (use the same logic as /enroll_audio security check)
                if enrollment_file_matches_user(f.name, user) and f.stat().st_size > 0:
                    recordings.append({
                        "filename": f.name,
                        "size": f.stat().st_size,
                        "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                    })
    
    # Enrollment prompt script (30-45 seconds to read)
    prompt_script = """Hello, my name is being recorded for voice identification purposes. 
I am speaking clearly and naturally to help the system recognize my voice in future recordings. 
This script contains a mix of short and long sentences to capture different aspects of my speech patterns. 
I will continue reading at a comfortable pace, ensuring each word is pronounced distinctly. 
The system needs to hear various sentence structures and vocal inflections to accurately identify me later. 
I am speaking in a normal conversational tone, not too fast and not too slow. 
This recording will be used to match my voice in meeting transcriptions and other audio files. 
I understand that clear pronunciation and natural speech will improve the accuracy of voice recognition. 
Thank you for listening to this enrollment recording."""
    
    return render_template("enroll.html", user=user, recordings=recordings, prompt_script=prompt_script)

@app.post("/upload_audio")
def upload_audio():
    """Handle enrollment audio upload"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 400

    f = request.files.get("audio")
    if not f:
        return jsonify({"error": "Missing audio file"}), 400

    filename = (f.filename or "audio").strip()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    # Get username - required for enrollment
    username = user.get("username", "").strip().lower()
    if not username:
        return jsonify({"error": "Username required. Please set your username first."}), 400
    
    # Save as "firstname,lastname.ext" format (or "firstname,lastname(2).ext" if exists)
    first = user.get("first", "").strip().lower()
    last = user.get("last", "").strip().lower()
    if not first or not last:
        return jsonify({"error": "First and last name required for enrollment."}), 400
    
    # Format: "firstname,lastname.ext"
    name_part = f"{first},{last}"
    base_filename = f"{name_part}{ext}"
    dest = ENROLL_DIR / base_filename
    counter = 1
    while dest.exists():
        counter += 1
        base_filename = f"{name_part}({counter}){ext}"
        dest = ENROLL_DIR / base_filename
    
    dest.parent.mkdir(parents=True, exist_ok=True)
    f.save(dest)

    return jsonify({"status": "saved", "filename": base_filename}), 200

@app.get("/enroll_audio/<filename>")
def enroll_audio(filename: str):
    """Serve enrollment audio file"""
    if not require_login():
        return ("Not logged in", 401), 401
    
    user = current_user()
    # Security: only allow accessing files that match user
    if not enrollment_file_matches_user(filename, user):
        return ("Unauthorized", 403), 403
    
    file_path = ENROLL_DIR / filename
    if file_path.exists():
        return send_from_directory(ENROLL_DIR, filename)
    
    return ("File not found", 404), 404

@app.post("/delete_recording")
def delete_recording():
    """Delete an enrollment recording"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    
    # Handle both JSON and FormData
    if request.is_json:
        filename = request.json.get("filename", "")
    else:
        filename = request.form.get("filename", "")
    
    if not filename:
        return jsonify({"error": "Missing filename"}), 400
    
    # Security: only allow deleting files that match user
    if not enrollment_file_matches_user(filename, user):
        return jsonify({"error": "Unauthorized"}), 403
    
    # Check how many valid recordings (>= 30 seconds) the user has
    enroll_dir = ENROLL_DIR
    valid_recordings = []
    if enroll_dir.exists():
        for f in enroll_dir.iterdir():
            if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                if enrollment_file_matches_user(f.name, user) and f.stat().st_size > 0:
                    duration = get_audio_duration(f)
                    if duration >= 30.0:
                        valid_recordings.append(f.name)
    
    # Prevent deleting if it would leave no valid recordings
    if len(valid_recordings) <= 1:
        return jsonify({"error": "Cannot delete this recording. You must have at least one enrollment recording that is 30 seconds or longer."}), 400
    
    file_path = ENROLL_DIR / filename
    if file_path.exists():
        file_path.unlink()
        return jsonify({"status": "deleted"}), 200
    
    return jsonify({"error": "File not found"}), 404

@app.post("/save_recordings")
def save_recordings():
    """Validate and save enrollment recordings"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 400
    
    username = user.get("username", "").strip().lower()
    if not username:
        return jsonify({"error": "Username not set"}), 400
    
    # Check that user has at least one recording >= 30 seconds
    enroll_dir = ENROLL_DIR
    valid_recordings = []
    if enroll_dir.exists():
        for f in enroll_dir.iterdir():
            if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                if enrollment_file_matches_user(f.name, user) and f.stat().st_size > 0:
                    duration = get_audio_duration(f)
                    if duration >= 30.0:
                        valid_recordings.append(f.name)
    
    if not valid_recordings:
        return jsonify({
            "error": "You must have at least one enrollment recording that is 30 seconds or longer. Please record or upload a longer audio file."
        }), 400
    
    return jsonify({"status": "saved"}), 200

@app.get("/record")
def record_meeting():
    """Page for recording/uploading meeting audio"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    
    # Enrollment check disabled - voice enrollment is optional
    has_enrollment = True
    
    organizations_directory = load_organizations_directory()
    return render_template("record_meeting.html", user=user, org_types=ORGANIZATION_TYPES, organizations_directory=organizations_directory, has_enrollment=has_enrollment)

@app.get("/upload")
def upload_meeting_get():
    """Page for uploading meeting audio file"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    
    # Enrollment check disabled - voice enrollment is optional
    has_enrollment = True
    
    organizations_directory = load_organizations_directory()
    return render_template("upload_meeting.html", user=user, org_types=ORGANIZATION_TYPES, organizations_directory=organizations_directory, has_enrollment=has_enrollment)

@app.post("/upload_meeting")
def upload_meeting():
    """Handle meeting audio upload and trigger pipeline"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 400
    
    # Enrollment check disabled - voice enrollment is optional
    
    f = request.files.get("audio")
    if not f:
        return jsonify({"error": "Missing audio"}), 400
    
    filename = (f.filename or "audio").strip()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400
    
    # Parse participants from form
    participants = []
    participants_json = request.form.get("participants", "")
    if participants_json:
        try:
            participants = json.loads(participants_json)
        except Exception:
            pass

    # Parse source organizations from form (organizations participants were selected from)
    source_organizations = []
    source_orgs_json = request.form.get("source_organizations", "")
    if source_orgs_json:
        try:
            source_organizations = json.loads(source_orgs_json)
        except Exception:
            # Fallback: try comma-separated string
            if isinstance(source_orgs_json, str):
                source_organizations = [org.strip() for org in source_orgs_json.split(",") if org.strip()]

    # Automatically add the current user (uploader) to participants if not already included
    user_email = user["email"].lower()
    participant_emails = []
    for p in participants:
        if isinstance(p, str):
            participant_emails.append(p.lower())
        elif isinstance(p, dict) and "email" in p:
            participant_emails.append(p["email"].lower())
    
    if user_email not in participant_emails:
        # Add user as participant
        participants.append({"email": user_email, "name": f"{user['first']} {user['last']}"})
    
    # Save to input directory with timestamp
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    meeting_name = request.form.get("meeting_name", "").strip()
    if meeting_name:
        # Sanitize meeting name for filename
        safe_name = "".join(c for c in meeting_name if c.isalnum() or c in (' ', '-', '_')).strip()[:50]
        safe_name = safe_name.replace(' ', '_')
        filename_base = f"{safe_name}_{timestamp}"
    else:
        filename_base = f"meeting_{timestamp}"
    
    meeting_path = INPUT_DIR / f"{filename_base}{ext}"
    f.save(meeting_path)

    # Create/update a meeting-processing job for UI progress tracking
    meeting_id = meeting_path.stem
    display_name = meeting_name or meeting_id
    try:
        _upsert_meeting_job(meeting_id, meeting_name=display_name, status="uploading", stage="uploading", percent=10, error=None)
        _append_meeting_job_log(meeting_id, f"[{datetime.now().isoformat()}] Upload received: {meeting_path.name}")
        _append_meeting_job_log(meeting_id, f"[{datetime.now().isoformat()}] Saved to: {meeting_path}")
        _upsert_meeting_job(meeting_id, meeting_name=display_name, status="processing", stage="queued", percent=10, error=None)
    except Exception:
        pass
    
    # Store upload timestamp for date logic
    upload_timestamp = datetime.now().isoformat()
    
    # Trigger pipeline in background thread with participants
    cfg = load_config()
    # Add uploader email to config so it can be used for cloud uploads
    cfg["uploader_email"] = user_email
    cfg["upload_timestamp"] = upload_timestamp  # Store upload timestamp for PDF date
    cfg["source_organizations"] = source_organizations  # Store source organizations for PDF header
    cfg["track_meeting_job"] = True
    cfg["meeting_id"] = meeting_id
    cfg["meeting_name"] = display_name
    threading.Thread(
        target=run_pipeline, 
        args=(meeting_path, cfg, participants),
        daemon=True
    ).start()
    
    return jsonify({
        "status": "processing",
        "filename": meeting_path.name,
        "meeting_id": meeting_id,
        "redirect": url_for("account_meetings"),
        "message": "Meeting uploaded successfully. Processing will begin shortly. You and all participants will receive an email when transcription is complete."
    }), 202


@app.get("/api/jobs/active")
def api_jobs_active():
    """Return active meeting-processing jobs (queued/uploading/processing)."""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    jobs = _list_active_meeting_jobs()
    # Small payload by default
    slim = [
        {
            "meeting_id": j.get("meeting_id"),
            "meeting_name": j.get("meeting_name"),
            "status": j.get("status"),
            "stage": j.get("stage"),
            "percent": j.get("percent"),
            "updated_at": j.get("updated_at"),
        }
        for j in jobs
    ]
    return jsonify({"jobs": slim}), 200


@app.get("/api/jobs/<meeting_id>")
def api_job_detail(meeting_id: str):
    """Return a meeting-processing job + log tail."""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    job = _load_meeting_job(meeting_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    try:
        tail = int(request.args.get("tail", "200"))
    except Exception:
        tail = 200
    tail = max(20, min(1000, tail))
    log_path = Path(job.get("log_path", ""))
    log_lines = _read_tail_lines(log_path, max_lines=tail)
    return jsonify({"job": job, "log_lines": log_lines}), 200

# API endpoints for organization/member search
@app.get("/api/search_organizations")
def api_search_organizations():
    """Search organizations by name (legacy - for member search)"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"organizations": []})
    
    matches = search_organizations(query)
    return jsonify({"organizations": matches})

@app.get("/api/search_organizations_directory")
def api_search_organizations_directory():
    """Search organization directory by name or abbreviation"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    query = request.args.get("q", "").strip()
    org_type = request.args.get("type", "").strip() or None
    
    if not query:
        return jsonify({"organizations": []})
    
    matches = search_organizations_directory(query, org_type)
    return jsonify({"organizations": matches})

@app.post("/api/add_organization")
def api_add_organization():
    """Add a new organization to the directory"""
    # Allow access during signup (no login required for adding to directory)
    
    # Handle both JSON and form data
    if request.is_json:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        abbreviation = (data.get("abbreviation") or "").strip()
        address = (data.get("address") or "").strip()
        org_type = (data.get("type") or "").strip()
    else:
        name = (request.form.get("org_name") or "").strip()
        abbreviation = (request.form.get("org_abbreviation") or "").strip()
        address_line = (request.form.get("org_address") or "").strip()
        city = (request.form.get("org_city") or "").strip()
        state = (request.form.get("org_state") or "").strip()
        zip_code = (request.form.get("org_zip") or "").strip()
        org_type = (request.form.get("org_type") or "").strip() or "other"
        
        if address_line and city and state and zip_code:
            address = f"{address_line}, {city}, {state} {zip_code}"
        else:
            address = address_line
    
    if not name or not address:
        return jsonify({"error": "Name and address are required", "success": False}), 400
    
    new_org = add_organization_to_directory(name, abbreviation, address, org_type)
    return jsonify({"organization": new_org, "success": True, "address": address}), 200

@app.get("/api/organization_members")
def api_organization_members():
    """Get members of an organization"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    org_name = request.args.get("org", "").strip()
    if not org_name:
        return jsonify({"members": []})
    
    member_emails = get_organization_members(org_name)
    users = read_users()
    
    members = []
    for email in member_emails:
        user = users.get(email.lower())
        if user:
            # Find the user's role in this organization
            user_orgs = user.get("organizations", [])
            role = None
            for org in user_orgs:
                if org.get("name") == org_name:
                    role = org.get("role")
                    break
            
            # Get all roles for this organization (support multiple roles)
            roles = []
            for org in user_orgs:
                if org.get("name") == org_name:
                    if "roles" in org and isinstance(org["roles"], list):
                        roles = org["roles"]
                    elif "role" in org and org["role"]:
                        roles = [org["role"]]
                    break
            
            members.append({
                "email": user["email"],
                "first": user["first"],
                "last": user["last"],
                "name": f"{user['first']} {user['last']}",
                "username": user.get("username", "").strip(),
                "role": roles[0] if roles else role,  # For backward compatibility
                "roles": roles  # All roles
            })
    
    return jsonify({"members": sorted(members, key=lambda x: (x["last"], x["first"]))})

@app.get("/api/user_by_username")
def api_user_by_username():
    """Get user information by username"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    username = request.args.get("username", "").strip().lower()
    if not username:
        return jsonify({"error": "Username required"}), 400
    
    users = read_users()
    
    # Find user by username
    for email, user_data in users.items():
        user_username = (user_data.get("username") or "").strip().lower()
        if user_username == username:
            return jsonify({
                "email": user_data["email"],
                "first": user_data.get("first", ""),
                "last": user_data.get("last", ""),
                "name": f"{user_data.get('first', '')} {user_data.get('last', '')}".strip() or username,
                "username": user_username
            })
    
    return jsonify({"error": "User not found"}), 404

@app.get("/upload_success")
def upload_success():
    """Upload success page"""
    if not require_login():
        return redirect(url_for("login_get"))
    return render_template("upload_success.html")

@app.get("/add_members")
def add_members():
    """Add members page for selecting participants from an organization"""
    if not require_login():
        return redirect(url_for("login_get"))
    
    org_name = request.args.get("org", "").strip()
    return_to = request.args.get("return_to", url_for("record_meeting"))
    
    if not org_name:
        flash("No organization specified.")
        return redirect(return_to)
    
    # Get members of the organization
    member_emails = get_organization_members(org_name)
    users = read_users()
    
    members = []
    for email in member_emails:
        user = users.get(email.lower())
        if user:
            # Find the user's role in this organization
            user_orgs = user.get("organizations", [])
            role = None
            for org in user_orgs:
                if org.get("name") == org_name:
                    role = org.get("role")
                    break
            
            # Get all roles for this organization (support multiple roles)
            roles = []
            for org in user_orgs:
                if org.get("name") == org_name:
                    if "roles" in org and isinstance(org["roles"], list):
                        roles = org["roles"]
                    elif "role" in org and org["role"]:
                        roles = [org["role"]]
                    break
            
            members.append({
                "email": user["email"],
                "first": user["first"],
                "last": user["last"],
                "name": f"{user['first']} {user['last']}",
                "username": user.get("username", "").strip(),
                "role": roles[0] if roles else role,  # For backward compatibility
                "roles": roles  # All roles
            })
    
    members = sorted(members, key=lambda x: (x["last"], x["first"]))
    
    return render_template("add_members.html", org_name=org_name, members=members, return_to=return_to)

@app.post("/account")
def account_post():
    """Update account settings"""
    if not require_login():
        return redirect(url_for("login_get"))

    user = current_user()
    if not user:
        return redirect(url_for("login_get"))

    users = read_users()
    old_email = user["email"]
    
    first = (request.form.get("first") or "").strip()
    last = (request.form.get("last") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    email2 = (request.form.get("email2") or "").strip().lower()

    if not first or not last:
        flash("First and last name required.")
        return redirect(url_for("account_get"))

    if email != email2 or not valid_email(email):
        flash("Emails must match and be valid.")
        return redirect(url_for("account_get"))

    if email != old_email and email in users:
        flash("That email is already taken.")
        return redirect(url_for("account_get"))
    
    # Handle password change
    password_hash = user["password_hash"]
    pw = request.form.get("password", "").strip()
    pw2 = request.form.get("password2", "").strip()
    if pw:
        if pw != pw2 or len(pw) < 8:
            flash("Passwords must match and be at least 8 characters.")
            return redirect(url_for("account_get"))
        password_hash = generate_password_hash(pw)

    # Handle removal of current positions
    remove_position_indices = request.form.get("remove_position_index", "").strip()
    current_organizations = user.get("organizations", [])
    
    if remove_position_indices:
        indices_to_remove = [int(i) for i in remove_position_indices.split(",") if i.strip().isdigit()]
        # Remove in reverse order to maintain indices
        indices_to_remove.sort(reverse=True)
        for idx in indices_to_remove:
            if 0 <= idx < len(current_organizations):
                current_organizations.pop(idx)
    
    # Parse new organizations from form
    organizations = list(current_organizations)  # Start with current positions
    org_count = int(request.form.get("org_count", "0") or "0")
    
    # Add new organizations from form
    valid_orgs = []
    for i in range(min(org_count, 10)):
        org_name = (request.form.get(f"org_name_{i}") or "").strip()
        org_type = (request.form.get(f"org_type_{i}") or "").strip()
        org_role = (request.form.get(f"org_role_{i}") or "").strip()
        if org_name and org_type and org_role:
            valid_orgs.append({
                "name": org_name,
                "type": org_type,
                "role": org_role
            })
    
    # Add new organizations to existing ones
    organizations.extend(valid_orgs)
    
    # Must have at least one organization total
    if not organizations:
        flash("You must have at least one organization. Please add at least one organization.")
        return redirect(url_for("account_get"))

    # Remove user from old organizations not in new list
    old_orgs = user.get("organizations", [])
    old_org_names = {org["name"] for org in old_orgs}
    new_org_names = {org["name"] for org in organizations}
    for org_name in old_org_names - new_org_names:
        remove_user_from_organization(org_name, old_email)
    
    # Update organizations.json for each new organization
    for org in organizations:
        add_user_to_organization(org["name"], org["type"], email)

    # Preserve username, connected_apps, and receive_meeting_emails before any email change
    preserved_username = user.get("username", "").strip().lower()
    preserved_connected_apps = user.get("connected_apps", {})
    
    # Get receive_meeting_emails preference from form (default to True if not set)
    receive_meeting_emails = request.form.get("receive_meeting_emails") == "on"

    # apply update
    if email != old_email:
        users.pop(old_email, None)
        session["user_email"] = email

    users[email] = {
        "first": first,
        "last": last,
        "email": email,
        "password_hash": password_hash,
        "organizations": organizations,
        "username": preserved_username,
        "connected_apps": preserved_connected_apps,
        "receive_meeting_emails": receive_meeting_emails
    }
    write_users(users)
    sync_emails_csv(users)

    flash("Account updated.")
    return redirect(url_for("account_home"))

@app.get("/meeting/<meeting_id>/transcript")
def meeting_transcript(meeting_id: str):
    """Interactive transcript view (HTML) built from utterances + speaker label map."""
    if not require_login():
        return ("Not logged in", 401)

    meeting = get_meeting(meeting_id) or {}

    utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
    if not utterances_path.exists():
        return ("Utterances not found", 404)

    try:
        utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
    except Exception:
        return ("Invalid utterances.json", 500)

    # Attempt to seed labels from identify_speakers output if present
    seeded_label_map: dict[str, str] = {}
    named_json_path = OUTPUT_DIR / f"{meeting_id}_named_script.json"
    if named_json_path.exists():
        try:
            labeled_rows = json.loads(named_json_path.read_text(encoding="utf-8"))
            for r in labeled_rows:
                diar = (r.get("diarization_speaker") or "").strip()
                spk = (r.get("speaker_name") or "").strip()
                if diar and spk and spk != "Unknown":
                    seeded_label_map[diar] = spk
        except Exception:
            seeded_label_map = {}

    # Build stable Unknown Speaker numbering based on first appearance order of diarization labels
    speakers_in_order: list[str] = []
    for u in utterances:
        s = (u.get("speaker") or "").strip()
        if not s:
            continue
        if s not in speakers_in_order:
            speakers_in_order.append(s)

    unknown_by_raw: dict[str, str] = {raw: f"Unknown Speaker {i+1}" for i, raw in enumerate(speakers_in_order)}
    raw_by_unknown: dict[str, str] = {v: k for k, v in unknown_by_raw.items()}

    stored_map: dict[str, str] = meeting.get("speaker_label_map", {}) if isinstance(meeting.get("speaker_label_map"), dict) else {}
    # Back-compat: older flows store Unknown Speaker N -> Name. Convert to raw-keyed mapping for display.
    stored_map_raw: dict[str, str] = {}
    for k, v in stored_map.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        key = k.strip()
        val = v.strip()
        if not val:
            continue
        if key in unknown_by_raw:
            stored_map_raw[key] = val
        elif key in raw_by_unknown:
            stored_map_raw[raw_by_unknown[key]] = val

    effective_map: dict[str, str] = dict(unknown_by_raw)
    effective_map.update(seeded_label_map)
    effective_map.update(stored_map_raw)

    # Build a compact speaker list for the sidebar
    speakers = [{"raw": raw, "display": effective_map.get(raw, raw)} for raw in speakers_in_order]

    # Apply splits + per-utterance overrides, and compute approximate confidence
    effective_utterances = _effective_utterances_for_meeting(meeting_id, meeting)

    rendered_utterances = []
    for u in effective_utterances:
        raw = (u.get("speaker") or "").strip()
        display_override = u.get("speaker_display_override")
        if isinstance(display_override, str):
            display_override = display_override.strip()
        else:
            display_override = ""
        utterance_dict = {
            "utterance_id": (u.get("utterance_id") or ""),
            "source_utterance_id": (u.get("source_utterance_id") or ""),
            "start": float(u.get("start", 0.0) or 0.0),
            "end": float(u.get("end", 0.0) or 0.0),
            "speaker_raw": raw,
            "speaker_display": (display_override or effective_map.get(raw, raw)),
            "speaker_confidence_percent": int(u.get("speaker_confidence_percent") or 0),
            "text": (u.get("text") or ""),
        }
        
        # Include word-level timestamps if available
        if "words" in u and isinstance(u["words"], list):
            utterance_dict["words"] = u["words"]
        
        rendered_utterances.append(utterance_dict)

    # Suggestions for autocomplete (usernames + full names)
    suggestions = []
    try:
        users = read_users()
        for email, u in users.items():
            username = (u.get("username") or "").strip()
            first = (u.get("first") or "").strip()
            last = (u.get("last") or "").strip()
            if username:
                suggestions.append(username)
            if first and last:
                suggestions.append(f"{first} {last}")
    except Exception:
        suggestions = []

    return render_template(
        "meeting_transcript.html",
        meeting_id=meeting_id,
        meeting=meeting,
        speakers=speakers,
        utterances=rendered_utterances,
        audio_url=url_for("meeting_audio", meeting_id=meeting_id),
        meeting_pdf_url=url_for("meeting_pdf", meeting_id=meeting_id),
        transcript_txt_url=url_for("meeting_transcript_txt", meeting_id=meeting_id),
        transcript_pdf_url=url_for("meeting_transcript_pdf", meeting_id=meeting_id),
        seeded_label_map=seeded_label_map,
        speaker_suggestions=sorted(list(set(suggestions)), key=lambda x: x.lower()),
    )


@app.get("/meeting/<meeting_id>/transcript.txt")
def meeting_transcript_txt(meeting_id: str):
    """Download labeled transcript text."""
    if not require_login():
        return ("Not logged in", 401)
    transcript_path = OUTPUT_DIR / f"{meeting_id}_named_script.txt"
    if transcript_path.exists():
        return send_from_directory(OUTPUT_DIR, transcript_path.name)
    return ("Transcript not found", 404)


@app.get("/meeting/<meeting_id>/transcript.pdf")
def meeting_transcript_pdf(meeting_id: str):
    """Download transcript-only PDF (not the meeting summary report)."""
    if not require_login():
        return "Not logged in", 401
    
    print(f"[TRANSCRIPT PDF] Request for meeting {meeting_id}")
    
    # Try the standard path
    pdf_path = OUTPUT_DIR / f"{meeting_id}_transcript.pdf"
    if pdf_path.exists() and pdf_path.is_file() and pdf_path.stat().st_size > 0:
        print(f"[TRANSCRIPT PDF] Found at: {pdf_path}")
        try:
            return send_from_directory(str(OUTPUT_DIR), pdf_path.name, as_attachment=False)
        except Exception as e:
            print(f"[TRANSCRIPT PDF] Error serving file: {e}")
            return f"Error serving PDF: {e}", 500
    
    # Check meeting metadata for transcript_pdf_path
    meeting = get_meeting(meeting_id)
    if meeting and meeting.get("transcript_pdf_path"):
        pdf_path_str = meeting["transcript_pdf_path"]
        print(f"[TRANSCRIPT PDF] Trying path from meeting metadata: {pdf_path_str}")
        
        if os.path.isabs(pdf_path_str):
            pdf_path = Path(pdf_path_str)
        else:
            pdf_path = ROOT / pdf_path_str
        
        if pdf_path.exists() and pdf_path.is_file() and pdf_path.stat().st_size > 0:
            print(f"[TRANSCRIPT PDF] Found at: {pdf_path}")
            try:
                return send_from_directory(str(pdf_path.parent), pdf_path.name, as_attachment=False)
            except Exception as e:
                print(f"[TRANSCRIPT PDF] Error serving file: {e}")
                return f"Error serving PDF: {e}", 500
    
    print(f"[TRANSCRIPT PDF] PDF not found for meeting {meeting_id}")
    return "Transcript PDF not found", 404

@app.get("/meeting/<meeting_id>/audio")
def meeting_audio(meeting_id: str):
    """Serve meeting audio file"""
    if not require_login():
        return ("Not logged in", 401), 401
    
    print(f"[AUDIO DOWNLOAD] Request for meeting {meeting_id}")
    
    # Check meetings.json to find the original audio file
    meetings = load_meetings()
    meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
    
    if meeting:
        # Try audio_path from meeting metadata
        if meeting.get("audio_path"):
            audio_path_str = meeting["audio_path"]
            print(f"[AUDIO DOWNLOAD] Trying audio_path from meeting: {audio_path_str}")
            
            # Handle both relative and absolute paths
            if os.path.isabs(audio_path_str):
                audio_path = Path(audio_path_str)
            else:
                audio_path = ROOT / audio_path_str
            
            if audio_path.exists() and audio_path.is_file():
                print(f"[AUDIO DOWNLOAD] Found audio at: {audio_path}")
                try:
                    return send_from_directory(str(audio_path.parent), audio_path.name, as_attachment=True)
                except Exception as e:
                    print(f"[AUDIO DOWNLOAD] Error serving file: {e}")
        
        # Try original_filename if audio_path doesn't work
        if meeting.get("original_filename"):
            original_filename = meeting["original_filename"]
            print(f"[AUDIO DOWNLOAD] Trying original_filename: {original_filename}")
            
            # Try in input directory first
            audio_path = INPUT_DIR / original_filename
            if audio_path.exists() and audio_path.is_file():
                print(f"[AUDIO DOWNLOAD] Found audio at: {audio_path}")
                try:
                    return send_from_directory(str(INPUT_DIR), original_filename, as_attachment=True)
                except Exception as e:
                    print(f"[AUDIO DOWNLOAD] Error serving file: {e}")
    
    # Fallback: try to find in input directory with common extensions
    print(f"[AUDIO DOWNLOAD] Trying fallback search in INPUT_DIR")
    for ext in [".webm", ".mp4", ".m4a", ".wav", ".mp3", ".mov", ".aac", ".flac", ".ogg"]:
        audio_path = INPUT_DIR / f"{meeting_id}{ext}"
        if audio_path.exists() and audio_path.is_file():
            print(f"[AUDIO DOWNLOAD] Found audio at: {audio_path}")
            try:
                return send_from_directory(str(INPUT_DIR), audio_path.name, as_attachment=True)
            except Exception as e:
                print(f"[AUDIO DOWNLOAD] Error serving file: {e}")
    
    # Also check output directory (sometimes audio is copied there)
    for ext in [".webm", ".mp4", ".m4a", ".wav", ".mp3", ".mov", ".aac", ".flac", ".ogg"]:
        audio_path = OUTPUT_DIR / f"{meeting_id}{ext}"
        if audio_path.exists() and audio_path.is_file():
            print(f"[AUDIO DOWNLOAD] Found audio at: {audio_path}")
            try:
                return send_from_directory(str(OUTPUT_DIR), audio_path.name, as_attachment=True)
            except Exception as e:
                print(f"[AUDIO DOWNLOAD] Error serving file: {e}")
    
    print(f"[AUDIO DOWNLOAD] Audio file not found for meeting {meeting_id}")
    return ("Audio file not found", 404), 404

@app.get("/meeting/<meeting_id>/pdf")
def meeting_pdf(meeting_id: str):
    """Serve meeting PDF transcript (meeting report)"""
    if not require_login():
        return ("Not logged in", 401), 401
    
    print(f"[MEETING PDF] Request for meeting {meeting_id}")
    
    # Check meeting metadata for pdf_path first
    meeting = get_meeting(meeting_id)
    if meeting and meeting.get("pdf_path"):
        pdf_path_str = meeting["pdf_path"]
        print(f"[MEETING PDF] Trying path from meeting metadata: {pdf_path_str}")
        
        if os.path.isabs(pdf_path_str):
            pdf_path = Path(pdf_path_str)
        else:
            pdf_path = ROOT / pdf_path_str
        
        if pdf_path.exists() and pdf_path.is_file() and pdf_path.stat().st_size > 0:
            print(f"[MEETING PDF] Found at: {pdf_path}")
            try:
                return send_from_directory(str(pdf_path.parent), pdf_path.name, as_attachment=False)
            except Exception as e:
                print(f"[MEETING PDF] Error serving file: {e}")
                return (f"Error serving PDF: {e}", 500), 500
    
    # Try meeting report first (standard path)
    pdf_path = OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf"
    if pdf_path.exists() and pdf_path.is_file() and pdf_path.stat().st_size > 0:
        print(f"[MEETING PDF] Found at: {pdf_path}")
        try:
            return send_from_directory(str(OUTPUT_DIR), f"{meeting_id}_meeting_report.pdf", as_attachment=False)
        except Exception as e:
            print(f"[MEETING PDF] Error serving file: {e}")
            return (f"Error serving PDF: {e}", 500), 500
    
    # Fallback to old transcript PDF format
    pdf_path = OUTPUT_DIR / f"{meeting_id}_transcript.pdf"
    if pdf_path.exists() and pdf_path.is_file() and pdf_path.stat().st_size > 0:
        print(f"[MEETING PDF] Found fallback at: {pdf_path}")
        try:
            return send_from_directory(str(OUTPUT_DIR), f"{meeting_id}_transcript.pdf", as_attachment=False)
        except Exception as e:
            print(f"[MEETING PDF] Error serving file: {e}")
            return (f"Error serving PDF: {e}", 500), 500
    
    print(f"[MEETING PDF] PDF not found for meeting {meeting_id}")
    return ("PDF not found", 404), 404

@app.post("/meeting/<meeting_id>/delete")
def delete_meeting(meeting_id: str):
    """Delete a meeting"""
    if not require_login():
        return ("Not logged in", 401), 401
    
    meetings = load_meetings()
    meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
    
    if not meeting:
        return ("Meeting not found", 404), 404
    
    # Check if user is a participant (basic authorization)
    user = current_user()
    if user and user["email"].lower() not in [p.lower() for p in meeting.get("participants", [])]:
        return ("Unauthorized", 403), 403
    
    # Remove from meetings list
    meetings = [m for m in meetings if m.get("id") != meeting_id]
    MEETINGS_JSON.write_text(json.dumps(meetings, indent=2), encoding="utf-8")
    
    return jsonify({"status": "deleted", "message": "Meeting deleted successfully"}), 200

def apply_speaker_labels_to_transcript(transcript_path: Path, label_map: dict) -> str:
    """
    Apply speaker labels to transcript text.
    Replaces "Unknown Speaker N" with user-provided names.
    
    Args:
        transcript_path: Path to transcript file
        label_map: Dict mapping "Unknown Speaker N" -> "New Name"
    
    Returns:
        Updated transcript text
    """
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")
    
    content = transcript_path.read_text(encoding="utf-8")
    
    # Replace each unknown speaker label
    for unknown_label, new_name in label_map.items():
        if new_name and new_name.strip():
            # Replace in format "Unknown Speaker N: text" or "Unknown Speaker N : text" (with optional spaces)
            # Match the speaker label followed by optional whitespace and a colon
            pattern = re.escape(unknown_label) + r"\s*:"
            replacement = new_name.strip() + ":"
            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    
    return content

def regenerate_meeting_pdf(meeting: dict, label_map: dict) -> Path:
    """
    Regenerate meeting PDF with new speaker labels.
    
    Args:
        meeting: Meeting dict from meetings.json
        label_map: Dict mapping "Unknown Speaker N" -> "New Name"
    
    Returns:
        Path to regenerated PDF
    """
    meeting_id = meeting.get("id")
    if not meeting_id:
        raise ValueError("Meeting ID is required")
    
    # Get transcript path
    transcript_path = None
    if meeting.get("transcript_path"):
        transcript_path = ROOT / meeting["transcript_path"]
    else:
        transcript_path = OUTPUT_DIR / f"{meeting_id}_named_script.txt"
    
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")
    
    # Apply labels to transcript
    updated_transcript = apply_speaker_labels_to_transcript(transcript_path, label_map)
    
    # Create temporary updated transcript file for PDF generation
    temp_transcript = OUTPUT_DIR / f"{meeting_id}_labeled_script_temp.txt"
    temp_transcript.write_text(updated_transcript, encoding="utf-8")
    
    try:
        # Generate PDF using meeting_pdf_summarizer
        pdf_path = OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf"
        summarizer_main = ROOT / "meeting_pdf_summarizer" / "main.py"
        roles_json = ROOT / "meeting_pdf_summarizer" / "roles.json"
        
        if not summarizer_main.exists():
            raise FileNotFoundError(f"PDF summarizer not found: {summarizer_main}")
        
        # Get upload date and source organizations from meeting
        upload_date = meeting.get("processed_at")
        source_orgs = meeting.get("source_organizations", [])
        source_orgs_str = ",".join(source_orgs) if source_orgs else ""
        
        PY = sys.executable
        cmd = [PY, str(summarizer_main),
               "--input", str(temp_transcript),
               "--output", str(pdf_path),
               "--roles", str(roles_json)]
        
        if upload_date:
            cmd.extend(["--upload-date", upload_date])
        if source_orgs_str:
            cmd.extend(["--source-organizations", source_orgs_str])
        
        # Run PDF generation
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"PDF generation failed: {result.stderr}")
        
        if not pdf_path.exists():
            raise RuntimeError("PDF was not created")
        
        return pdf_path
        
    finally:
        # Clean up temporary transcript
        if temp_transcript.exists():
            try:
                temp_transcript.unlink()
            except Exception:
                pass

@app.post("/api/meetings/<meeting_id>/label_speakers")
def label_speakers(meeting_id: str):
    """Label unknown speakers and regenerate PDF"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    # Get meeting
    meeting = get_meeting(meeting_id)
    if not meeting:
        return jsonify({"error": "Meeting not found"}), 404
    
    # Check authorization (user must be a participant)
    user_email = user["email"].lower()
    participants = [p.lower() if isinstance(p, str) else p.get("email", "").lower() for p in meeting.get("participants", [])]
    if user_email not in participants:
        return jsonify({"error": "Unauthorized: You are not a participant in this meeting"}), 403
    
    # Get labels from request
    data = request.get_json()
    if not data or "labels" not in data:
        return jsonify({"error": "Labels are required"}), 400
    
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        return jsonify({"error": "Labels must be a dictionary"}), 400
    
    # Validate labels (trim whitespace, check for duplicates)
    label_map = {}
    seen_names = set()
    for unknown_speaker, new_name in labels.items():
        if not isinstance(unknown_speaker, str) or not unknown_speaker.startswith("Unknown Speaker"):
            continue
        
        new_name = (new_name or "").strip()
        if new_name:
            # Check for duplicates
            if new_name.lower() in seen_names:
                return jsonify({
                    "error": "Duplicate name",
                    "message": f"'{new_name}' is assigned to multiple speakers. Each speaker must have a unique name."
                }), 400
            seen_names.add(new_name.lower())
            label_map[unknown_speaker] = new_name
    
    if not label_map:
        return jsonify({"error": "No valid labels provided"}), 400
    
    # Results tracking
    results = {
        "pdf_regenerated": False,
        "email_sent": False,
        "uploads": {},
        "errors": []
    }
    
    try:
        # Convert "Unknown Speaker N" labels to raw diarization labels
        # First, get utterances to map Unknown Speaker N -> raw labels
        utterances_path = OUTPUT_DIR / f"{meeting_id}_utterances.json"
        if not utterances_path.exists():
            return jsonify({"error": "Utterances not found"}), 404
        
        try:
            utterances = json.loads(utterances_path.read_text(encoding="utf-8"))
        except Exception as e:
            return jsonify({"error": f"Invalid utterances.json: {e}"}), 500
        
        if not isinstance(utterances, list):
            return jsonify({"error": "utterances.json must be a list"}), 500
        
        # Build mapping from Unknown Speaker N to raw diarization labels
        unknown_by_raw, raw_by_unknown, speakers_in_order = _unknown_map_from_utterances(utterances)
        
        # Convert label_map from "Unknown Speaker N" -> "Name" to raw -> "Name"
        raw_label_map: dict[str, str] = {}
        for unknown_speaker, new_name in label_map.items():
            if unknown_speaker in raw_by_unknown:
                raw_label = raw_by_unknown[unknown_speaker]
                raw_label_map[raw_label] = new_name.strip()
            elif unknown_speaker in unknown_by_raw.values():
                # Already a raw label or find it
                for raw, unknown in unknown_by_raw.items():
                    if unknown == unknown_speaker:
                        raw_label_map[raw] = new_name.strip()
                        break
        
        if not raw_label_map:
            return jsonify({"error": "No valid labels could be mapped to diarization speakers"}), 400
        
        print(f"[LABEL SPEAKERS] Mapped labels: {label_map} -> raw labels: {raw_label_map}")
        
        # Get existing label map and merge
        existing_map = meeting.get("speaker_label_map", {}) if isinstance(meeting.get("speaker_label_map"), dict) else {}
        normalized_existing: dict[str, str] = {}
        for kk, vv in existing_map.items():
            if isinstance(kk, str) and isinstance(vv, str) and vv.strip():
                normalized_existing[kk.strip()] = vv.strip()
        
        normalized_existing.update(raw_label_map)
        
        labels_version = int(meeting.get("labels_version", 0) or 0) + 1
        
        # Regenerate ALL assets (TXT, JSON, transcript PDF, meeting report PDF)
        print(f"[LABEL SPEAKERS] Regenerating all assets for meeting {meeting_id}")
        try:
            assets = _build_labeled_script_from_utterances(meeting_id, meeting, normalized_existing)
            _write_named_script_assets(assets["named_txt_path"], assets["named_json_path"], assets["rows"])
            transcript_pdf = _regenerate_transcript_pdf_from_named_json(meeting_id, assets["named_json_path"])
            meeting_report_pdf = _regenerate_meeting_report_pdf_from_transcript(meeting_id, meeting, assets["named_txt_path"])
            
            results["pdf_regenerated"] = bool(meeting_report_pdf)
            print(f"[LABEL SPEAKERS] Regenerated: TXT={assets['named_txt_path'].exists()}, JSON={assets['named_json_path'].exists()}, Transcript PDF={bool(transcript_pdf)}, Meeting PDF={bool(meeting_report_pdf)}")
        except Exception as e:
            error_msg = f"Failed to regenerate assets: {e}"
            print(f"[LABEL SPEAKERS] ERROR: {error_msg}")
            import traceback
            print(traceback.format_exc())
            results["errors"].append(error_msg)
            return jsonify({"error": "Regeneration failed", "message": str(e)}), 500
        
        # Update meeting metadata with all paths
        update_data = {
            "speaker_label_map": normalized_existing,
            "labels_updated_at": datetime.now().isoformat(),
            "labels_version": labels_version,
            "transcript_path": str((OUTPUT_DIR / f"{meeting_id}_named_script.txt").relative_to(ROOT)),
            "transcript_updated_at": datetime.now().isoformat(),
        }
        
        if transcript_pdf:
            update_data["transcript_pdf_path"] = str(transcript_pdf.relative_to(ROOT))
        
        if meeting_report_pdf:
            update_data["pdf_path"] = str(meeting_report_pdf.relative_to(ROOT))
            update_data["pdf_updated_at"] = datetime.now().isoformat()
        
        update_meeting(meeting_id, update_data)
        
        # Reload meeting to get updated data
        meeting = get_meeting(meeting_id)
        
        # 5. Re-send emails to participants (if meeting report PDF was generated)
        participant_emails = meeting.get("participants", [])
        if participant_emails and meeting_report_pdf:
            meeting_name = meeting.get("name") or meeting.get("id", "Meeting")
            subject = f"Updated meeting report (speaker labels added): {meeting_name}"
            
            # Build email body
            body = f"""Hello,

The meeting report for "{meeting_name}" has been updated with speaker labels.

The following unknown speakers have been labeled:
"""
            for unknown, name in label_map.items():
                body += f"  • {unknown} → {name}\n"
            
            body += f"""

You can view the updated report in your Past Meetings.

Best regards,
Phi AI
"""
            
            email_success_count = 0
            for email in participant_emails:
                email_str = email.lower() if isinstance(email, str) else email.get("email", "").lower()
                if email_str:
                    try:
                        if send_email(email_str, subject, body, attachments=[meeting_report_pdf]):
                            email_success_count += 1
                            print(f"[LABEL SPEAKERS] Email sent to {email_str}")
                        else:
                            results["errors"].append(f"Failed to send email to {email_str}")
                    except Exception as e:
                        results["errors"].append(f"Error sending email to {email_str}: {e}")
            
            if email_success_count > 0:
                results["email_sent"] = True
        
        # 6. Re-upload to connected apps for all participants (if PDFs were generated)
        meeting_name = meeting.get("name") or meeting.get("id", "Meeting")
        transcript_path_for_upload = OUTPUT_DIR / f"{meeting_id}_named_script.txt"
        if not transcript_path_for_upload.exists():
            transcript_path_for_upload = None
        
        upload_success = {}
        if meeting_report_pdf:
            for email in participant_emails:
                email_str = email.lower() if isinstance(email, str) else email.get("email", "").lower()
                if not email_str:
                    continue
                
                try:
                    upload_to_connected_apps(
                        email_str,
                        meeting_report_pdf,
                        transcript_path_for_upload,
                        meeting_name
                    )
                    upload_success[email_str] = True
                    print(f"[LABEL SPEAKERS] Uploaded to connected apps for {email_str}")
                except Exception as e:
                    upload_success[email_str] = False
                    results["errors"].append(f"Upload failed for {email_str}: {e}")
                    print(f"[LABEL SPEAKERS] Warning: Upload failed for {email_str}: {e}")
        
        results["uploads"] = upload_success
        
        # Build summary message
        summary_parts = []
        if results["pdf_regenerated"]:
            summary_parts.append("PDF regenerated ✅")
        if results["email_sent"]:
            summary_parts.append("Email sent ✅")
        for email, success in results["uploads"].items():
            if success:
                summary_parts.append(f"Uploaded to {email} ✅")
            else:
                summary_parts.append(f"Upload to {email} ❌")
        
        if results["errors"]:
            summary_parts.append(f"\n{len(results['errors'])} error(s) occurred")
        
        return jsonify({
            "status": "success",
            "message": "Speaker labels updated successfully",
            "summary": "\n".join(summary_parts),
            "results": results,
            "meeting": meeting,
            "regenerated": {
                "named_script_txt": True,
                "named_script_json": True,
                "transcript_pdf": bool(transcript_pdf),
                "meeting_report_pdf": bool(meeting_report_pdf),
            }
        }), 200
        
    except Exception as e:
        error_msg = f"Failed to update speaker labels: {e}"
        print(f"[LABEL SPEAKERS] ERROR: {error_msg}")
        import traceback
        print(traceback.format_exc())
        return jsonify({
            "error": "Update failed",
            "message": error_msg,
            "results": results
        }), 500

def delete_user_account(user_id: str) -> dict:
    """
    Bulletproof user account deletion service.
    
    Policy: Option A (Recommended)
    - Delete user account + detach personal tokens
    - Keep meeting artifacts in "retained" state if shared with other users
    - Delete meetings only if user was the sole participant
    
    This function performs all deletion operations and returns a summary.
    Should be called within proper error handling and logging context.
    
    Args:
        user_id: User email (lowercase)
    
    Returns:
        dict with keys:
            - deleted_files: list of deleted file paths
            - deleted_meetings: list of meeting IDs deleted
            - retained_meetings: list of meeting IDs retained (shared)
            - deleted_chat_sessions: count of deleted chat sessions
            - deleted_chat_messages: count of deleted chat messages
            - revoked_tokens: list of revoked app names
            - errors: list of error messages
            - summary: dict with counts and details
    
    DELETE PROFILE QA CHECKLIST:
    ============================
    
    Manual Testing:
    ---------------
    [ ] 1. Delete profile returns success
        - Navigate to Account Settings → Delete Account
        - Type "DELETE" when prompted
        - Verify success message and redirect
    
    [ ] 2. User is logged out immediately
        - After deletion, verify user cannot access /account pages
        - Verify redirect to home page
        - Verify session cookie is cleared
    
    [ ] 3. Re-login fails (or shows account deleted)
        - Attempt to login with deleted user's email
        - Verify login fails with appropriate error
    
    [ ] 4. Tokens are removed
        - Before deletion: connect Dropbox/Box/Google Drive
        - Delete account
        - Verify tokens are cleared from users.csv
        - Verify no further uploads happen (check logs)
    
    [ ] 5. Shared meetings remain accessible to other users
        - Create meeting with User A and User B as participants
        - Delete User A
        - Verify User B can still access the meeting
        - Verify meeting files are not deleted
    
    [ ] 6. Sole-owner meetings are deleted
        - Create meeting with only User A as participant
        - Delete User A
        - Verify meeting files are deleted from output/
        - Verify meeting removed from meetings.json
    
    [ ] 7. Chat sessions are deleted
        - Create chat sessions for user
        - Delete account
        - Verify chat_sessions.json no longer contains user's sessions
        - Verify chat_messages.json no longer contains user's messages
    
    [ ] 8. Enrollment files are deleted
        - Upload enrollment audio files
        - Delete account
        - Verify enrollment files are removed from enroll/
    
    [ ] 9. Organization memberships are removed
        - Add user to organization
        - Delete user account
        - Verify user removed from organization in organizations.json
    
    [ ] 10. CSRF protection works
        - Attempt to delete account without proper session
        - Verify request is rejected
    
    [ ] 11. Confirmation requirement works
        - Attempt to delete without typing "DELETE"
        - Verify deletion is blocked
    
    Automated Testing (if implemented):
    -----------------------------------
    [ ] Unit test: delete_user_account() removes user from users.csv
    [ ] Unit test: delete_user_account() deletes enrollment files
    [ ] Unit test: delete_user_account() removes user from organizations
    [ ] Unit test: delete_user_account() deletes sole-owner meetings
    [ ] Unit test: delete_user_account() retains shared meetings
    [ ] Unit test: delete_user_account() deletes chat sessions/messages
    [ ] Unit test: delete_user_account() revokes tokens
    [ ] Integration test: Full deletion flow with all data types
    [ ] Security test: CSRF protection prevents unauthorized deletion
    [ ] Security test: Only authenticated user can delete their own account
    
    Referential Integrity Checks:
    -----------------------------
    [ ] No orphaned meeting references (meetings.json participants)
    [ ] No orphaned organization members (organizations.json)
    [ ] No orphaned chat sessions (chat_sessions.json)
    [ ] No orphaned chat messages (chat_messages.json)
    [ ] No orphaned enrollment files (enroll/ directory)
    [ ] No orphaned meeting files (output/ directory)
    
    Logging Verification:
    --------------------
    [ ] Deletion attempt is logged with timestamp
    [ ] Summary includes all deletion counts
    [ ] Errors are logged with details
    [ ] Token revocation is logged
    [ ] Meeting retention/deletion decisions are logged
    """
    user_email = user_id.lower()
    users = read_users()
    user = users.get(user_email)
    
    if not user:
        return {
            "deleted_files": [],
            "deleted_meetings": [],
            "retained_meetings": [],
            "deleted_chat_sessions": 0,
            "deleted_chat_messages": 0,
            "revoked_tokens": [],
            "errors": ["User not found"],
            "summary": {}
        }
    
    username = user.get("username", "").strip().lower()
    first = user.get("first", "").strip()
    last = user.get("last", "").strip()
    
    deleted_files = []
    deleted_meetings = []
    retained_meetings = []
    revoked_tokens = []
    errors = []
    
    # 1. Revoke connected app tokens (Dropbox, Box, Google Drive)
    connected_apps = user.get("connected_apps", {})
    for app_name in list(connected_apps.keys()):
        try:
            # Clear tokens from user record (they're encrypted, but we remove them)
            # The tokens will be removed when we delete the user record
            revoked_tokens.append(app_name)
            print(f"[DELETE] Revoked {app_name} tokens for {user_email}")
        except Exception as e:
            errors.append(f"Failed to revoke {app_name} tokens: {e}")
            print(f"[DELETE] Warning: Could not revoke {app_name} tokens: {e}")
    
    # 2. Delete ALL enrollment audio files
    enroll_dir = ENROLL_DIR
    enrollment_count = 0
    if enroll_dir.exists():
        for f in list(enroll_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                if enrollment_file_matches_user(f.name, user):
                    try:
                        f.unlink()
                        deleted_files.append(f"enrollment: {f.name}")
                        enrollment_count += 1
                        print(f"[DELETE] Deleted enrollment file: {f.name}")
                    except Exception as e:
                        error_msg = f"Could not delete enrollment file {f.name}: {e}"
                        errors.append(error_msg)
                        print(f"[DELETE] Warning: {error_msg}")
    
    # 3. Delete chat sessions and messages
    chat_sessions_deleted = 0
    chat_messages_deleted = 0
    try:
        sessions_dict = load_chat_sessions()
        messages_dict = load_chat_messages()
        
        # Get all session IDs for this user
        user_sessions = sessions_dict.get(user_email, [])
        session_ids_to_delete = [s["id"] for s in user_sessions]
        
        # Delete sessions
        if user_email in sessions_dict:
            chat_sessions_deleted = len(sessions_dict[user_email])
            del sessions_dict[user_email]
            save_chat_sessions(sessions_dict)
            print(f"[DELETE] Deleted {chat_sessions_deleted} chat sessions for {user_email}")
        
        # Delete messages for all sessions
        for session_id in session_ids_to_delete:
            if session_id in messages_dict:
                chat_messages_deleted += len(messages_dict[session_id])
                del messages_dict[session_id]
        
        if session_ids_to_delete:
            save_chat_messages(messages_dict)
            print(f"[DELETE] Deleted {chat_messages_deleted} chat messages for {user_email}")
    except Exception as e:
        error_msg = f"Failed to delete chat sessions/messages: {e}"
        errors.append(error_msg)
        print(f"[DELETE] Warning: {error_msg}")
    
    # 4. Remove user from all organizations
    orgs = load_organizations()
    orgs_removed_from = []
    for org_name in list(orgs.keys()):
        if user_email in [m.lower() for m in orgs[org_name].get("members", [])]:
            remove_user_from_organization(org_name, user_email)
            orgs_removed_from.append(org_name)
            print(f"[DELETE] Removed {user_email} from organization: {org_name}")
    
    # 5. Process meetings (Policy: Option A - retain if shared, delete if sole owner)
    meetings = load_meetings()
    updated_meetings = []
    
    for meeting in meetings:
        participants = meeting.get("participants", [])
        # Filter out the user's email from participants
        updated_participants = []
        for p in participants:
            if isinstance(p, str):
                if p.lower() != user_email:
                    updated_participants.append(p)
            elif isinstance(p, dict):
                if p.get("email", "").lower() != user_email:
                    updated_participants.append(p)
        
        meeting_id = meeting.get("id") or meeting.get("stem", "")
        
        # If no participants left, delete meeting (user was sole owner)
        if not updated_participants:
            deleted_meetings.append(meeting_id)
            # Delete meeting output files
            if meeting_id:
                try:
                    transcript_pdf = OUTPUT_DIR / f"{meeting_id}_transcript.pdf"
                    transcript_txt = OUTPUT_DIR / f"{meeting_id}_transcript.txt"
                    utterances_json = OUTPUT_DIR / f"{meeting_id}_utterances.json"
                    named_script = OUTPUT_DIR / f"{meeting_id}_named_script.txt"
                    aai_json = OUTPUT_DIR / f"{meeting_id}_aai.json"
                    named_script_json = OUTPUT_DIR / f"{meeting_id}_named_script.json"
                    meeting_wav = OUTPUT_DIR / f"{meeting_id}_16k.wav"
                    
                    for file_path in [transcript_pdf, transcript_txt, utterances_json, named_script, aai_json, named_script_json, meeting_wav]:
                        if file_path.exists():
                            file_path.unlink()
                            deleted_files.append(f"meeting: {file_path.name}")
                    print(f"[DELETE] Deleted meeting {meeting_id} (sole owner)")
                except Exception as e:
                    error_msg = f"Could not delete meeting files for {meeting_id}: {e}"
                    errors.append(error_msg)
                    print(f"[DELETE] Warning: {error_msg}")
        else:
            # Meeting has other participants - retain it, just remove user
            retained_meetings.append(meeting_id)
            meeting["participants"] = updated_participants
            updated_meetings.append(meeting)
            print(f"[DELETE] Retained meeting {meeting_id} (shared with {len(updated_participants)} other participants)")
    
    # Update meetings.json (only with meetings that still have participants)
    MEETINGS_JSON.write_text(json.dumps(updated_meetings, indent=2), encoding="utf-8")
    
    # 6. Delete user's custom vocabulary
    vocab_dict = load_vocabulary()
    vocab_deleted = 0
    if user_email in vocab_dict:
        vocab_deleted = len(vocab_dict[user_email])
        del vocab_dict[user_email]
        save_vocabulary(vocab_dict)
        print(f"[DELETE] Deleted {vocab_deleted} custom vocabulary entries for {user_email}")
    
    # 7. Delete user from users.csv (hard delete)
    if user_email in users:
        del users[user_email]
        write_users(users)
        print(f"[DELETE] Deleted user record from users.csv: {user_email}")
    
    # Build summary
    summary = {
        "user_email": user_email,
        "username": username,
        "enrollment_files_deleted": enrollment_count,
        "chat_sessions_deleted": chat_sessions_deleted,
        "chat_messages_deleted": chat_messages_deleted,
        "vocabulary_deleted": vocab_deleted,
        "organizations_removed_from": len(orgs_removed_from),
        "meetings_deleted": len(deleted_meetings),
        "meetings_retained": len(retained_meetings),
        "tokens_revoked": len(revoked_tokens),
        "total_files_deleted": len(deleted_files),
        "errors_count": len(errors)
    }
    
    return {
        "deleted_files": deleted_files,
        "deleted_meetings": deleted_meetings,
        "retained_meetings": retained_meetings,
        "deleted_chat_sessions": chat_sessions_deleted,
        "deleted_chat_messages": chat_messages_deleted,
        "deleted_vocabulary": vocab_deleted,
        "revoked_tokens": revoked_tokens,
        "errors": errors,
        "summary": summary
    }

@app.post("/account/delete")
def delete_account():
    """
    Delete user account endpoint with CSRF protection and confirmation.
    
    Security:
    - Requires authentication
    - Requires CSRF token validation
    - Requires confirmation (user must type "DELETE")
    - Only authenticated user can delete their own account
    """
    # Authentication check
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    user_email = user["email"].lower()
    
    # CSRF protection: verify request came from authenticated session
    # (Flask session cookies provide basic CSRF protection, but we add explicit check)
    session_email = session.get("user_email", "").lower()
    if session_email != user_email:
        return jsonify({"error": "CSRF validation failed: session mismatch"}), 403
    
    # Get request data
    data = request.get_json() or {}
    confirmation = (data.get("confirmation") or "").strip()
    
    # Require explicit confirmation
    if confirmation != "DELETE":
        return jsonify({
            "error": "Confirmation required",
            "message": "You must type 'DELETE' (all caps) to confirm account deletion"
        }), 400
    
    # Log deletion attempt
    print(f"[DELETE] Account deletion initiated for {user_email} at {datetime.now().isoformat()}")
    
    try:
        # Perform deletion (all operations in this function)
        result = delete_user_account(user_email)
        
        # Clear session immediately after deletion
        session.clear()
        
        # Log comprehensive summary
        summary = result["summary"]
        print(f"[DELETE] Account deletion completed for {user_email}")
        print(f"[DELETE] Summary:")
        print(f"  - Enrollment files: {summary['enrollment_files_deleted']}")
        print(f"  - Chat sessions: {summary['chat_sessions_deleted']}")
        print(f"  - Chat messages: {summary['chat_messages_deleted']}")
        print(f"  - Organizations removed from: {summary['organizations_removed_from']}")
        print(f"  - Meetings deleted: {summary['meetings_deleted']}")
        print(f"  - Meetings retained (shared): {summary['meetings_retained']}")
        print(f"  - Tokens revoked: {summary['tokens_revoked']} ({', '.join(result['revoked_tokens'])})")
        print(f"  - Total files deleted: {summary['total_files_deleted']}")
        if result["errors"]:
            print(f"  - Errors: {len(result['errors'])}")
            for err in result["errors"]:
                print(f"    * {err}")
        
        return jsonify({
            "status": "deleted",
            "message": "Account deleted successfully",
            "summary": summary,
            "errors": result["errors"] if result["errors"] else None
        }), 200
        
    except Exception as e:
        # Log error but don't expose internal details
        error_msg = f"Account deletion failed: {e}"
        print(f"[DELETE] ERROR: {error_msg}")
        import traceback
        print(traceback.format_exc())
        
        # Clear session even on error (user should be logged out)
        session.clear()
        
        return jsonify({
            "error": "Deletion failed",
            "message": "An error occurred during account deletion. Please contact support if this persists."
        }), 500

@app.get("/account/connect_apps")
def connect_apps_get():
    """Connect Apps page"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    
    # Get connected apps
    connected_apps = user.get("connected_apps", {})
    
    # Get Box diagnostics if Box is connected
    box_diagnostics = None
    if connected_apps.get("box"):
        try:
            from services.box_client import get_box_diagnostics
            box_diagnostics = get_box_diagnostics(user["email"].lower())
        except Exception as e:
            print(f"[Box] Error getting diagnostics: {e}")
    
    return render_template("connect_apps.html", user=user, connected_apps=connected_apps, box_diagnostics=box_diagnostics)

@app.get("/connect/dropbox/confirm")
def connect_dropbox_confirm():
    """Dedicated page to complete Dropbox connection after login"""
    if not require_login():
        return redirect(url_for("login_get"))
    
    user = current_user()
    return render_template("connect_dropbox_confirm.html", user=user)

@app.get("/connect/googledrive/confirm")
def connect_googledrive_confirm():
    """Dedicated page to complete Google Drive connection after login"""
    if not require_login():
        return redirect(url_for("login_get"))
    
    user = current_user()
    return render_template("connect_googledrive_confirm.html", user=user)

@app.get("/connect/box/confirm")
def connect_box_confirm():
    """Dedicated page to complete Box connection after login"""
    if not require_login():
        return redirect(url_for("login_get"))
    
    user = current_user()
    return render_template("connect_box_confirm.html", user=user)

# Dropbox OAuth Routes
@app.get("/connect/dropbox/authorize")
def dropbox_authorize():
    """Initiate Dropbox OAuth flow"""
    if not require_login():
        # Store that we want to connect Dropbox after login
        session['oauth_connect_dropbox'] = True
        return redirect(url_for("login_get"))
    
    DROPBOX_CLIENT_ID = os.getenv("DROPBOX_CLIENT_ID")
    if not DROPBOX_CLIENT_ID:
        flash("Dropbox integration not configured. Please contact support.")
        return redirect(url_for("connect_apps_get"))
    
    # Generate state token for CSRF protection
    user = current_user()
    user_email = user["email"] if user else ""
    state = secrets.token_urlsafe(32)
    session['dropbox_oauth_state'] = state
    session['dropbox_oauth_user'] = user_email  # Store user email in case session expires
    
    # Dropbox OAuth URL
    # Use localhost explicitly for local development to match Dropbox settings
    redirect_uri = url_for('dropbox_callback', _external=True)
    # Request offline access to get long-lived tokens (token_type='offline' in Dropbox means requesting offline.access scope)
    # Normalize to localhost if it's 127.0.0.1
    if '127.0.0.1' in redirect_uri:
        redirect_uri = redirect_uri.replace('127.0.0.1', 'localhost')
    
    # Required scopes for file upload
    # files.content.write - Upload files
    # files.content.read - Read files (for verification)
    # account_info.read - Basic account info
    scope = "files.content.write files.content.read account_info.read"
    
    # Request offline access to get refresh tokens (token rotation)
    # token_access_type=offline enables token rotation which provides refresh tokens
    dropbox_auth_url = (
        "https://www.dropbox.com/oauth2/authorize?"
        f"client_id={DROPBOX_CLIENT_ID}&"
        f"response_type=code&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        f"scope={scope}&"
        f"token_access_type=offline"
    )
    
    return redirect(dropbox_auth_url)

@app.get("/connect/dropbox/callback")
def dropbox_callback():
    """Handle Dropbox OAuth callback"""
    # Get state and code from request
    state = request.args.get('state')
    code = request.args.get('code')
    error = request.args.get('error')
    
    # If not logged in, redirect to login and then to connect confirmation page
    if not require_login():
        session['oauth_connect_dropbox_after_login'] = True
        flash("Please log in to complete Dropbox connection.")
        return redirect(url_for("login_get"))
    
    # Now we're logged in - verify state token
    stored_state = session.get('dropbox_oauth_state')
    if state != stored_state:
        print(f"State mismatch: received={state}, stored={stored_state}")
        flash("Invalid state token. Please try again.")
        return redirect(url_for("connect_apps_get"))
    
    if error:
        print(f"Dropbox OAuth error: {error}")
        flash(f"Dropbox authorization failed: {error}")
        return redirect(url_for("connect_apps_get"))
    
    if not code:
        print("No authorization code received from Dropbox")
        flash("Authorization failed. Please try again.")
        return redirect(url_for("connect_apps_get"))
    
    DROPBOX_CLIENT_ID = os.getenv("DROPBOX_CLIENT_ID")
    DROPBOX_CLIENT_SECRET = os.getenv("DROPBOX_CLIENT_SECRET")
    
    if not DROPBOX_CLIENT_ID or not DROPBOX_CLIENT_SECRET:
        print("Dropbox credentials missing from .env")
        flash("Dropbox integration not configured.")
        return redirect(url_for("connect_apps_get"))
    
    # Exchange code for access token
    try:
        # Use localhost explicitly for local development to match Dropbox settings
        redirect_uri = url_for('dropbox_callback', _external=True)
        # Normalize to localhost if it's 127.0.0.1
        if '127.0.0.1' in redirect_uri:
            redirect_uri = redirect_uri.replace('127.0.0.1', 'localhost')
        
        print(f"Exchanging code for token with redirect_uri: {redirect_uri}")
        
        # Dropbox token endpoint uses form data, not Basic Auth
        token_response = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "client_id": DROPBOX_CLIENT_ID,
                "client_secret": DROPBOX_CLIENT_SECRET
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10
        )
        
        print(f"[INFO] Dropbox token response status: {token_response.status_code}")
        if token_response.status_code != 200:
            print(f"[DEBUG] Token response: {token_response.text[:500]}")
        
        if token_response.status_code != 200:
            error_data = token_response.json() if token_response.text else {}
            error_msg = error_data.get("error_description", error_data.get("error", "Unknown error"))
            print(f"[ERROR] Dropbox token exchange failed: {error_msg}")
            flash(f"Failed to connect Dropbox: {error_msg}")
            return redirect(url_for("connect_apps_get"))
        
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")  # Available with token_access_type=offline
        expires_in = token_data.get("expires_in", 14400)  # Default 4 hours (14400 seconds)
        account_id = token_data.get("account_id")  # Optional but helpful for identification
        
        if not access_token:
            print("[ERROR] No access token in Dropbox response")
            flash("Failed to get access token from Dropbox.")
            return redirect(url_for("connect_apps_get"))
        
        # Warn if no refresh token (shouldn't happen with token_access_type=offline, but check anyway)
        if not refresh_token:
            print("[WARN] Dropbox OAuth did not return a refresh token. Token rotation may not work. User may need to reconnect.")
        
        # Encrypt and store tokens
        user = current_user()
        if not user:
            print("[ERROR] User not found in session")
            flash("Session expired. Please log in again.")
            return redirect(url_for("login_get"))
        
        users = read_users()
        
        if "connected_apps" not in users[user["email"]]:
            users[user["email"]]["connected_apps"] = {}
        
        encrypted_token = encrypt_token(access_token)
        encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None
        
        # Calculate expiration timestamp (expires_in is in seconds, subtract 2 min buffer)
        expires_at = int(time.time()) + expires_in - 120
        
        users[user["email"]]["connected_apps"]["dropbox"] = {
            "access_token_encrypted": encrypted_token,
            "refresh_token_encrypted": encrypted_refresh,  # Store refresh token for token rotation
            "token_expires_at": expires_at,  # Unix timestamp for expiration check
            "account_id": account_id,  # Optional: store account ID for identification
            "folder_path": "/PhiAI/Meetings",
            "connected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "needs_reauth": False  # Clear needs_reauth flag on successful connection
        }
        
        write_users(users)
        print(f"[SUCCESS] Dropbox connected for {user['email']}, token expires in {expires_in}s (at {expires_at})")
        flash("Dropbox connected successfully!")
        
        # Clear OAuth state from session
        session.pop('dropbox_oauth_state', None)
        session.pop('dropbox_oauth_user', None)
        session.pop('oauth_return_to', None)
        
        return redirect(url_for("connect_apps_get"))
    except requests.exceptions.RequestException as e:
        print(f"Network error connecting Dropbox: {e}")
        flash(f"Network error: {str(e)}")
        return redirect(url_for("connect_apps_get"))
    except Exception as e:
        print(f"Error connecting Dropbox: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error connecting Dropbox: {str(e)}")
        return redirect(url_for("connect_apps_get"))

@app.post("/connect/dropbox/disconnect")
def dropbox_disconnect():
    """Disconnect Dropbox"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    users = read_users()
    user_email = user["email"].lower()
    
    if user_email not in users:
        return jsonify({"error": "User not found"}), 404
    
    if "connected_apps" not in users[user_email]:
        users[user_email]["connected_apps"] = {}
    
    users[user_email]["connected_apps"].pop("dropbox", None)
    write_users(users)
    
    return jsonify({"status": "disconnected"}), 200

@app.post("/connect/dropbox/update")
def dropbox_update():
    """Update Dropbox folder path"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    folder_path = (request.form.get("folder_path") or "").strip()
    if not folder_path:
        return jsonify({"error": "Folder path required"}), 400
    
    user = current_user()
    users = read_users()
    
    if "connected_apps" not in users[user["email"]]:
        return jsonify({"error": "Dropbox not connected"}), 400
    
    if "dropbox" not in users[user["email"]]["connected_apps"]:
        return jsonify({"error": "Dropbox not connected"}), 400
    
    users[user["email"]]["connected_apps"]["dropbox"]["folder_path"] = folder_path
    write_users(users)
    
    return jsonify({"status": "updated"}), 200

# Google Drive OAuth Routes
@app.get("/connect/googledrive/authorize")
def googledrive_authorize():
    """Initiate Google Drive OAuth flow"""
    if not require_login():
        # Store that we want to connect Google Drive after login
        session['oauth_connect_googledrive'] = True
        return redirect(url_for("login_get"))
    
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_DRIVE_CLIENT_ID")
    if not GOOGLE_CLIENT_ID:
        flash("Google Drive integration not configured.")
        return redirect(url_for("connect_apps_get"))
    
    # Generate state token for CSRF protection
    user = current_user()
    user_email = user.get("email", "").strip().lower()
    
    state = secrets.token_urlsafe(32)
    session['googledrive_oauth_state'] = state
    session['googledrive_oauth_user'] = user_email  # Store user email in case session expires
    
    # Google Drive OAuth URL
    # Use localhost explicitly for local development to match Google settings
    redirect_uri = url_for('googledrive_callback', _external=True)
    # Normalize to localhost if it's 127.0.0.1
    if '127.0.0.1' in redirect_uri:
        redirect_uri = redirect_uri.replace('127.0.0.1', 'localhost')
    
    google_auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code&"
        f"scope=https://www.googleapis.com/auth/drive.file&"
        f"state={state}&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    
    return redirect(google_auth_url)

@app.get("/connect/googledrive/callback")
def googledrive_callback():
    """Handle Google Drive OAuth callback"""
    # Get state and code from request
    state = request.args.get('state')
    code = request.args.get('code')
    error = request.args.get('error')
    
    # If not logged in, redirect to login and then to connect confirmation page
    if not require_login():
        session['oauth_connect_googledrive_after_login'] = True
        # Store the full callback URL to restore after login
        session['oauth_return_to'] = request.url
        flash("Please log in to complete Google Drive connection.")
        return redirect(url_for("login_get"))
    
    # Verify state token
    if state != session.get('googledrive_oauth_state'):
        flash("Invalid state token.")
        return redirect(url_for("connect_apps_get"))
    
    # Clear state after verification
    session.pop('googledrive_oauth_state', None)
    session.pop('googledrive_oauth_user', None)
    session.pop('oauth_return_to', None)
    
    code = request.args.get('code')
    if not code:
        flash("Authorization failed.")
        return redirect(url_for("connect_apps_get"))
    
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_DRIVE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET")
    
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        flash("Google Drive integration not configured.")
        return redirect(url_for("connect_apps_get"))
    
    try:
        # Normalize redirect URI to match authorization request
        redirect_uri = url_for('googledrive_callback', _external=True)
        if '127.0.0.1' in redirect_uri:
            redirect_uri = redirect_uri.replace('127.0.0.1', 'localhost')
        
        print(f"Exchanging code for token with redirect_uri: {redirect_uri}")
        
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }
        )
        
        if token_response.status_code != 200:
            flash("Failed to connect Google Drive.")
            return redirect(url_for("connect_apps_get"))
        
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        
        if not access_token:
            flash("Failed to get access token from Google Drive.")
            return redirect(url_for("connect_apps_get"))
        
        user = current_user()
        users = read_users()
        
        if "connected_apps" not in users[user["email"]]:
            users[user["email"]]["connected_apps"] = {}
        
        encrypted_token = encrypt_token(access_token)
        encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None
        
        users[user["email"]]["connected_apps"]["googledrive"] = {
            "access_token_encrypted": encrypted_token,
            "refresh_token_encrypted": encrypted_refresh,
            "folder_name": "PhiAI Meetings",
            "connected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        }
        
        write_users(users)
        
        flash("Google Drive connected successfully!")
        return redirect(url_for("connect_apps_get"))
    except Exception as e:
        print(f"Error connecting Google Drive: {e}")
        flash(f"Error connecting Google Drive: {str(e)}")
        return redirect(url_for("connect_apps_get"))

@app.post("/connect/googledrive/disconnect")
def googledrive_disconnect():
    """Disconnect Google Drive"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    users = read_users()
    user_email = user["email"].lower()
    
    if user_email not in users:
        return jsonify({"error": "User not found"}), 404
    
    if "connected_apps" not in users[user_email]:
        users[user_email]["connected_apps"] = {}
    
    users[user_email]["connected_apps"].pop("googledrive", None)
    write_users(users)
    
    return jsonify({"status": "disconnected"}), 200

@app.post("/connect/googledrive/update")
def googledrive_update():
    """Update Google Drive folder name"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    folder_name = (request.form.get("folder_name") or "").strip()
    if not folder_name:
        return jsonify({"error": "Folder name required"}), 400
    
    user = current_user()
    users = read_users()
    
    if "connected_apps" not in users[user["email"]] or "googledrive" not in users[user["email"]]["connected_apps"]:
        return jsonify({"error": "Google Drive not connected"}), 400
    
    users[user["email"]]["connected_apps"]["googledrive"]["folder_name"] = folder_name
    write_users(users)
    
    return jsonify({"status": "updated"}), 200

# Box OAuth Routes
@app.get("/connect/box/authorize")
def box_authorize():
    """Initiate Box OAuth flow"""
    if not require_login():
        # Store that we want to connect Box after login
        session['oauth_connect_box'] = True
        return redirect(url_for("login_get"))
    
    BOX_CLIENT_ID = os.getenv("BOX_CLIENT_ID")
    if not BOX_CLIENT_ID:
        flash("Box integration not configured.")
        return redirect(url_for("connect_apps_get"))
    
    # Generate state token for CSRF protection
    user = current_user()
    user_email = user.get("email", "").strip().lower()
    
    state = secrets.token_urlsafe(32)
    session['box_oauth_state'] = state
    session['box_oauth_user'] = user_email  # Store user email in case session expires
    
    # Box OAuth URL
    # Use localhost explicitly for local development to match Box settings
    redirect_uri = url_for('box_callback', _external=True)
    # Normalize to localhost if it's 127.0.0.1
    if '127.0.0.1' in redirect_uri:
        redirect_uri = redirect_uri.replace('127.0.0.1', 'localhost')
    
    # Box requires redirect_uri to be URL-encoded in the authorization URL
    # Use url_encode (which is quote) from imports
    redirect_uri_encoded = url_encode(redirect_uri, safe='')
    
    print(f"[Box OAuth] Redirect URI: {redirect_uri}")
    print(f"[Box OAuth] Redirect URI (encoded): {redirect_uri_encoded}")
    print(f"[Box OAuth] Client ID: {BOX_CLIENT_ID}")
    
    # Box OAuth: Scopes are configured in Box Developer Console, not passed in URL
    # The app must have "Read and write all files and folders" scope enabled in Box Developer Console
    # No scope parameter needed - Box uses the app's configured scopes
    
    box_auth_url = (
        "https://account.box.com/api/oauth2/authorize?"
        f"client_id={BOX_CLIENT_ID}&"
        f"response_type=code&"
        f"redirect_uri={redirect_uri_encoded}&"
        f"state={state}"
    )
    
    print(f"[Box OAuth] Full authorization URL: {box_auth_url}")
    
    return redirect(box_auth_url)

@app.get("/connect/box/callback")
def box_callback():
    """Handle Box OAuth callback"""
    # Get state and code from request
    state = request.args.get('state')
    code = request.args.get('code')
    error = request.args.get('error')
    
    # If not logged in, redirect to login and then to connect confirmation page
    if not require_login():
        session['oauth_connect_box_after_login'] = True
        # Store the full callback URL to restore after login
        session['oauth_return_to'] = request.url
        flash("Please log in to complete Box connection.")
        return redirect(url_for("login_get"))
    
    # Verify state token
    if state != session.get('box_oauth_state'):
        flash("Invalid state token.")
        return redirect(url_for("connect_apps_get"))
    
    # Clear state after verification
    session.pop('box_oauth_state', None)
    session.pop('box_oauth_user', None)
    session.pop('oauth_return_to', None)
    
    if error:
        error_description = request.args.get('error_description', '')
        if error == 'invalid_scope':
            flash(
                f"Box authorization failed: Invalid scope. "
                f"Please configure 'Read and write all files and folders' scope in Box Developer Console → Your App → Configuration → Application Scopes. "
                f"Then try connecting again.",
                "error"
            )
        else:
            flash(f"Box authorization failed: {error}. {error_description}", "error")
        return redirect(url_for("connect_apps_get"))
    
    if not code:
        flash("Authorization failed.")
        return redirect(url_for("connect_apps_get"))
    
    BOX_CLIENT_ID = os.getenv("BOX_CLIENT_ID")
    BOX_CLIENT_SECRET = os.getenv("BOX_CLIENT_SECRET")
    
    if not BOX_CLIENT_ID or not BOX_CLIENT_SECRET:
        flash("Box integration not configured.")
        return redirect(url_for("connect_apps_get"))
    
    try:
        # Normalize redirect URI to match authorization request
        redirect_uri = url_for('box_callback', _external=True)
        if '127.0.0.1' in redirect_uri:
            redirect_uri = redirect_uri.replace('127.0.0.1', 'localhost')
        
        print(f"Exchanging code for token with redirect_uri: {redirect_uri}")
        
        token_response = requests.post(
            "https://api.box.com/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": BOX_CLIENT_ID,
                "client_secret": BOX_CLIENT_SECRET,
                "redirect_uri": redirect_uri
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if token_response.status_code != 200:
            flash("Failed to connect Box.")
            return redirect(url_for("connect_apps_get"))
        
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)  # Default 1 hour (3600 seconds)
        
        if not access_token:
            print("[ERROR] No access token in Box response")
            flash("Failed to get access token from Box.")
            return redirect(url_for("connect_apps_get"))
        
        # Store token temporarily to verify scopes
        user = current_user()
        users = read_users()
        
        if "connected_apps" not in users[user["email"]]:
            users[user["email"]]["connected_apps"] = {}
        
        # Temporarily store token for scope verification
        encrypted_token = encrypt_token(access_token)
        encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None
        expires_at = int(time.time()) + expires_in - 120
        
        users[user["email"]]["connected_apps"]["box"] = {
            "access_token_encrypted": encrypted_token,
            "refresh_token_encrypted": encrypted_refresh,
            "token_expires_at": expires_at,
            "folder_name": "PhiAI Meetings",
            "connected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "needs_reauth": False,
            "needs_scope_update": False
        }
        write_users(users)
        
        # CRITICAL: Verify token has required scopes by testing write permissions
        print("[Box] Verifying token has required scopes...")
        from services.box_client import verify_write_scope
        
        has_write_scope, scope_error = verify_write_scope(user["email"], force_check=True)
        if not has_write_scope:
            # Mark as needing scope update
            users[user["email"]]["connected_apps"]["box"]["needs_scope_update"] = True
            write_users(users)
            
            error_msg = (
                "Box connection failed: Your Box app doesn't have the required scopes configured. "
                "CRITICAL STEPS (must do BEFORE connecting): "
                "1) Go to https://developer.box.com/ → My Apps → Your App → Configuration → Application Scopes. "
                "2) Enable 'Read and write all files and folders stored in Box'. "
                "3) Click Save Changes and wait a few minutes for changes to propagate. "
                "4) Then try connecting again. "
                "The token will only work if scopes are configured in Box Developer Console FIRST."
            )
            print(f"[Box] {error_msg}")
            flash(error_msg, "error")
            return redirect(url_for("connect_apps_get"))
        
        print("[Box] Token scope verification passed - token has required permissions")
        
        # Update connection status (already stored above, just update flags)
        users[user["email"]]["connected_apps"]["box"]["needs_scope_update"] = False
        users[user["email"]]["connected_apps"]["box"]["needs_reauth"] = False
        write_users(users)
        
        print(f"[Box] Connected for {user['email']}, token expires in {expires_in}s (at {expires_at})")
        
        # Check write scope status
        if has_write_scope:
            flash("Box connected successfully! Write permissions verified.")
        else:
            flash("Box connected, but write permissions verification failed. See the checklist below for fix instructions.", "warning")
        
        return redirect(url_for("connect_apps_get"))
    except Exception as e:
        print(f"Error connecting Box: {e}")
        flash(f"Error connecting Box: {str(e)}")
        return redirect(url_for("connect_apps_get"))

@app.post("/connect/box/disconnect")
def box_disconnect():
    """Disconnect Box"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    users = read_users()
    user_email = user["email"].lower()
    
    if user_email not in users:
        return jsonify({"error": "User not found"}), 404
    
    if "connected_apps" not in users[user_email]:
        users[user_email]["connected_apps"] = {}
    
    users[user_email]["connected_apps"].pop("box", None)
    write_users(users)
    
    return jsonify({"status": "disconnected"}), 200

@app.post("/connect/box/recheck")
def box_recheck():
    """Recheck Box write permissions (force check)"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    user_email = user["email"].lower()
    
    from services.box_client import verify_write_scope, get_box_diagnostics
    
    print(f"[Box] Rechecking write permissions for {user_email}...")
    has_write, error_msg = verify_write_scope(user_email, force_check=True)
    
    diagnostics = get_box_diagnostics(user_email)
    
    if has_write:
        return jsonify({
            "status": "success",
            "message": "Write permissions verified successfully",
            "diagnostics": diagnostics
        }), 200
    else:
        return jsonify({
            "status": "error",
            "message": error_msg or "Write permissions verification failed",
            "diagnostics": diagnostics
        }), 200

@app.post("/connect/box/update")
def box_update():
    """Update Box folder name"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    folder_name = (request.form.get("folder_name") or "").strip()
    if not folder_name:
        return jsonify({"error": "Folder name required"}), 400
    
    user = current_user()
    users = read_users()
    
    if "connected_apps" not in users[user["email"]] or "box" not in users[user["email"]]["connected_apps"]:
        return jsonify({"error": "Box not connected"}), 400
    
    users[user["email"]]["connected_apps"]["box"]["folder_name"] = folder_name
    write_users(users)
    
    return jsonify({"status": "updated"}), 200

# ----------------------------
# Ask Phi (Ollama Chat)
# ----------------------------
@app.get("/ask")
def ask_get():
    """Ask Phi chat interface"""
    if not require_login():
        return redirect(url_for("login_get"))
    
    user = current_user()
    chat_sessions = get_user_chat_sessions(user["email"])
    
    # Check Ollama health
    try:
        from integrations.ollama_client import check_ollama_health, check_model_available
        is_healthy, health_error = check_ollama_health()
        model_available, model_error = check_model_available()
        ollama_ready = is_healthy and model_available
        ollama_error = health_error or model_error
    except Exception as e:
        ollama_ready = False
        ollama_error = f"Error checking Ollama: {str(e)}"
    
    return render_template("ask.html", user=user, chat_sessions=chat_sessions, ollama_ready=ollama_ready, ollama_error=ollama_error)

@app.post("/api/ask")
def ask_post():
    """Handle chat message and return streaming response with conversation memory"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    data = request.get_json()
    message = data.get("message", "").strip()
    session_id = data.get("session_id")
    
    if not message:
        return jsonify({"error": "Message is required"}), 400
    
    # Create session if needed
    if not session_id:
        session_id = create_chat_session(user["email"], message[:50])
    
    # Save user message
    add_chat_message(session_id, "user", message)
    
    # Get conversation history (last 10 messages for context)
    conversation_history = get_chat_messages(session_id)
    # Exclude the message we just added (it's already in history)
    # Get last 10 messages before this one for context
    recent_messages = conversation_history[:-1][-10:] if len(conversation_history) > 1 else []
    
    # Build conversation context from history
    conversation_context = ""
    if recent_messages:
        conversation_context = "Previous conversation:\n"
        for msg in recent_messages:
            role_label = "User" if msg["role"] == "user" else "Phi"
            conversation_context += f"{role_label}: {msg['content']}\n\n"
    
    # Retrieve meeting context (smarter retrieval based on conversation)
    try:
        from services.meeting_retrieval import retrieve_meeting_context_smart
        # Combine current message with conversation context for better retrieval
        query_context = message
        if recent_messages:
            # Include recent conversation to understand context better
            recent_user_messages = [m["content"] for m in recent_messages if m["role"] == "user"][-3:]
            query_context = " ".join(recent_user_messages) + " " + message
        
        meeting_context = retrieve_meeting_context_smart(user["email"], query_context, conversation_history=recent_messages)
    except Exception as e:
        meeting_context = f"Error retrieving meeting context: {str(e)}"
    
    # Enhanced system prompt for intelligent, conversational Phi
    system_prompt = """You are Phi, an intelligent AI assistant for Phi AI. You help users understand and navigate their meeting history through natural, conversational interactions.

Your personality and capabilities:
- You are friendly, helpful, and conversational - like ChatGPT or Claude
- You maintain context across the conversation and remember what was discussed
- You can have multi-turn conversations, ask clarifying questions when needed, and build on previous exchanges
- You're knowledgeable about the user's meetings and can synthesize information across multiple meetings
- You think step-by-step and provide thoughtful, well-reasoned responses

Meeting data access:
- You have access to the user's past meeting transcripts, summaries, participants, and metadata
- Always ground your answers in the actual meeting data provided
- Never hallucinate or make up meeting facts
- If you're unsure about something, say so and explain what meetings you checked
- When referencing meetings, include the meeting title and date for verification

Response style:
- Be natural and conversational, not robotic
- Use clear, well-structured responses with appropriate formatting
- Use bullet points for lists, action items, or multiple items
- Break up long responses into readable paragraphs
- Ask follow-up questions when helpful or when you need clarification
- Reference specific meetings by name and date when citing information
- Be concise but thorough - provide enough detail to be helpful

Special capabilities:
- You can summarize meetings, extract action items, find specific discussions, and answer questions about what was decided
- You can compare information across multiple meetings
- You can help users track follow-ups, decisions, and action items over time
- You understand time references (today, this week, last month, etc.) and can filter meetings accordingly

Remember: You're having a conversation. Build on previous messages, maintain context, and engage naturally while being helpful and accurate."""
    
    # Build full context with conversation history and meeting data
    full_context = ""
    if conversation_context:
        full_context += conversation_context + "\n"
    if meeting_context and "No meetings" not in meeting_context and "Error" not in meeting_context:
        full_context += "Meeting Data Available:\n" + meeting_context + "\n\n"
    
    # Generate response with Ollama (with conversation history)
    try:
        from integrations.ollama_client import generate_conversational_response
        
        def generate():
            full_response = ""
            for chunk in generate_conversational_response(
                message=message,
                system_prompt=system_prompt,
                context=full_context if full_context else None,
                conversation_history=recent_messages,
                stream=True
            ):
                full_response += chunk
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            
            # Save assistant response
            add_chat_message(session_id, "assistant", full_response)
            yield f"data: {json.dumps({'done': True})}\n\n"
        
        return Response(stream_with_context(generate()), mimetype="text/event-stream")
    
    except Exception as e:
        error_msg = f"Error generating response: {str(e)}"
        add_chat_message(session_id, "assistant", error_msg)
        return jsonify({"error": error_msg}), 500

@app.get("/api/chat_sessions")
def get_chat_sessions():
    """Get all chat sessions for current user"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    sessions = get_user_chat_sessions(user["email"])
    return jsonify({"sessions": sessions})

@app.post("/api/chat_sessions")
def create_chat_session_api():
    """Create a new chat session"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    data = request.get_json()
    title = data.get("title", "New Chat")
    
    session_id = create_chat_session(user["email"], title)
    return jsonify({"session_id": session_id, "title": title})

@app.get("/api/chat_sessions/<session_id>")
def get_chat_session(session_id: str):
    """Get messages for a specific chat session"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    
    # Verify session belongs to user
    sessions = get_user_chat_sessions(user["email"])
    if not any(s.get("id") == session_id for s in sessions):
        return jsonify({"error": "Session not found"}), 404
    
    messages = get_chat_messages(session_id)
    return jsonify({"messages": messages})

@app.delete("/api/chat_sessions/<session_id>")
def delete_chat_session(session_id: str):
    """Delete a chat session and its messages"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    
    # Verify session belongs to user
    sessions_dict = load_chat_sessions()
    user_sessions = sessions_dict.get(user["email"], [])
    
    if not any(s.get("id") == session_id for s in user_sessions):
        return jsonify({"error": "Session not found"}), 404
    
    # Remove session from user's sessions
    sessions_dict[user["email"]] = [s for s in user_sessions if s.get("id") != session_id]
    save_chat_sessions(sessions_dict)
    
    # Remove messages
    messages_dict = load_chat_messages()
    if session_id in messages_dict:
        del messages_dict[session_id]
        save_chat_messages(messages_dict)
    
    return jsonify({"status": "deleted"}), 200

# ----------------------------
# Custom Vocabulary Routes
# ----------------------------
@app.get("/settings/vocabulary")
def vocabulary_get():
    """Custom Vocabulary management page"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template("settings_vocabulary.html", user=user)

@app.get("/api/vocabulary")
def vocabulary_list():
    """Get user's vocabulary entries (supports search, filter, sort)"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    user_email = user["email"].lower()
    
    entries = get_user_vocabulary(user_email)
    
    # Apply search filter
    search = request.args.get("search", "").strip().lower()
    if search:
        entries = [
            e for e in entries
            if search in e.get("term", "").lower() or
               search in e.get("definition", "").lower() or
               any(search in str(a).lower() for a in e.get("aliases", []))
        ]
    
    # Apply type filter
    vocab_type = request.args.get("type", "").strip()
    if vocab_type:
        entries = [e for e in entries if e.get("vocab_type") == vocab_type]
    
    # Apply sort
    sort_by = request.args.get("sort", "term")  # term, created_at
    if sort_by == "term":
        entries.sort(key=lambda e: e.get("term", "").lower())
    elif sort_by == "created_at":
        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    
    return jsonify({"entries": entries, "count": len(entries)}), 200

@app.post("/api/vocabulary")
def vocabulary_create():
    """Create a new vocabulary entry"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    user_email = user["email"].lower()
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400
    
    term = (data.get("term") or "").strip()
    if not term:
        return jsonify({"error": "Term is required"}), 400
    
    if len(term) > 80:
        return jsonify({"error": "Term must be 80 characters or less"}), 400
    
    definition = (data.get("definition") or "").strip()
    if len(definition) > 500:
        return jsonify({"error": "Definition must be 500 characters or less"}), 400
    
    vocab_type = (data.get("vocab_type") or "").strip() or None
    pronunciation = (data.get("pronunciation") or "").strip() or None
    aliases_str = (data.get("aliases") or "").strip()
    
    # Parse aliases (comma-separated)
    aliases = []
    if aliases_str:
        aliases = [a.strip() for a in aliases_str.split(",") if a.strip()]
    
    # Check for duplicates (case-insensitive)
    existing_entries = get_user_vocabulary(user_email)
    term_normalized = term.lower().strip()
    for existing in existing_entries:
        if existing.get("term", "").lower().strip() == term_normalized:
            return jsonify({
                "error": "Duplicate term",
                "message": f"This term already exists: {existing.get('term')}",
                "existing_id": existing.get("id")
            }), 400
    
    # Create new entry
    import secrets
    new_entry = {
        "id": secrets.token_urlsafe(16),
        "term": term,
        "term_normalized": term_normalized,
        "definition": definition or None,
        "vocab_type": vocab_type,
        "pronunciation": pronunciation,
        "aliases": aliases,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    
    existing_entries.append(new_entry)
    save_user_vocabulary(user_email, existing_entries)
    
    return jsonify({"entry": new_entry, "message": "Vocabulary term added successfully"}), 201

@app.put("/api/vocabulary/<entry_id>")
def vocabulary_update(entry_id: str):
    """Update a vocabulary entry"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    user_email = user["email"].lower()
    
    entries = get_user_vocabulary(user_email)
    entry_index = None
    for i, e in enumerate(entries):
        if e.get("id") == entry_id:
            entry_index = i
            break
    
    if entry_index is None:
        return jsonify({"error": "Entry not found"}), 404
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400
    
    term = (data.get("term") or "").strip()
    if not term:
        return jsonify({"error": "Term is required"}), 400
    
    if len(term) > 80:
        return jsonify({"error": "Term must be 80 characters or less"}), 400
    
    definition = (data.get("definition") or "").strip()
    if len(definition) > 500:
        return jsonify({"error": "Definition must be 500 characters or less"}), 400
    
    vocab_type = (data.get("vocab_type") or "").strip() or None
    pronunciation = (data.get("pronunciation") or "").strip() or None
    aliases_str = (data.get("aliases") or "").strip()
    
    # Parse aliases
    aliases = []
    if aliases_str:
        aliases = [a.strip() for a in aliases_str.split(",") if a.strip()]
    
    # Check for duplicates (excluding current entry)
    term_normalized = term.lower().strip()
    for i, existing in enumerate(entries):
        if i != entry_index and existing.get("term", "").lower().strip() == term_normalized:
            return jsonify({
                "error": "Duplicate term",
                "message": f"This term already exists: {existing.get('term')}"
            }), 400
    
    # Update entry
    entries[entry_index].update({
        "term": term,
        "term_normalized": term_normalized,
        "definition": definition or None,
        "vocab_type": vocab_type,
        "pronunciation": pronunciation,
        "aliases": aliases,
        "updated_at": datetime.now().isoformat()
    })
    
    save_user_vocabulary(user_email, entries)
    
    return jsonify({"entry": entries[entry_index], "message": "Vocabulary term updated successfully"}), 200

@app.delete("/api/vocabulary/<entry_id>")
def vocabulary_delete(entry_id: str):
    """Delete a vocabulary entry"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    user_email = user["email"].lower()
    
    entries = get_user_vocabulary(user_email)
    original_count = len(entries)
    entries = [e for e in entries if e.get("id") != entry_id]
    
    if len(entries) == original_count:
        return jsonify({"error": "Entry not found"}), 404
    
    save_user_vocabulary(user_email, entries)
    
    return jsonify({"message": "Vocabulary term deleted successfully"}), 200

# ----------------------------
# Dev-only watcher (limited scope)
# ----------------------------
DEV_WATCH_OBSERVER = None
DEV_WATCH_LAST_RESTART = 0.0

def _restart_dev_server():
    global DEV_WATCH_LAST_RESTART
    now = time.time()
    if now - DEV_WATCH_LAST_RESTART < 1.0:
        return
    DEV_WATCH_LAST_RESTART = now
    print("[DEV WATCH] Change detected. Restarting dev server...")
    try:
        subprocess.Popen([sys.executable] + sys.argv, cwd=str(ROOT))
    finally:
        os._exit(0)

def start_dev_watcher() -> None:
    global DEV_WATCH_OBSERVER
    if os.getenv("DIO_DEV_WATCH", "1") != "1":
        return
    if DEV_WATCH_OBSERVER is not None:
        return

    ignore_patterns = [
        "**/.venv/**",
        "**/venv/**",
        "**/site-packages/**",
        "**/__pycache__/**",
        "**/output/**",
        "**/input/**",
        "**/.git/**",
        "**/.cursor/**",
        # Runtime data files that change during normal app usage.
        # If we watch these, the dev server restarts mid-request (causing browser "connection reset").
        "**/organizations.json",
        "**/organizations_directory.json",
        "**/reset_tokens.json",
        "**/vocabulary.json",
        "**/config.json",
    ]
    patterns = ["*.py", "*.html", "*.css", "*.js", "*.json", "*.txt"]

    handler = PatternMatchingEventHandler(
        patterns=patterns,
        ignore_patterns=ignore_patterns,
        ignore_directories=False
    )

    def _should_ignore_path(path_str: str) -> bool:
        if not path_str:
            return True
        norm_path = os.path.normcase(path_str)
        # Ignore runtime files regardless of where they are written
        runtime_basenames = {
            "organizations.json",
            "organizations_directory.json",
            "reset_tokens.json",
            "vocabulary.json",
            "config.json",
        }
        try:
            if os.path.basename(norm_path) in runtime_basenames:
                return True
        except Exception:
            pass
        parts = set(Path(norm_path).parts)
        if any(p in parts for p in {".venv", "venv", "site-packages", "__pycache__", "output", "input", ".git", ".cursor"}):
            return True
        # Ignore build artifacts and temp files
        if norm_path.endswith((".pyc", ".pyo", ".tmp", ".log")):
            return True
        return False

    def on_any_event(event):
        if event.is_directory:
            return
        if _should_ignore_path(event.src_path):
            return
        _restart_dev_server()

    handler.on_any_event = on_any_event
    observer = Observer()
    watch_paths = [str(ROOT), str(TEMPLATES), str(STATIC_DIR)]
    for watch_path in watch_paths:
        if os.path.exists(watch_path):
            observer.schedule(handler, watch_path, recursive=True)

    observer.daemon = True
    observer.start()
    DEV_WATCH_OBSERVER = observer
    print("[DEV WATCH] Limited watcher enabled (excludes .venv/site-packages).")

if __name__ == "__main__":
    ensure_dirs()
    start_upload_worker()
    start_dev_watcher()
    app.run(debug=True, host="127.0.0.1", port=5000, use_reloader=False)

