import csv
import json
import os
import re
import secrets
import shutil
import smtplib
import ssl
import subprocess
import sys
import threading
import time
import base64
import requests
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
from urllib.parse import quote as url_encode
from werkzeug.security import generate_password_hash, check_password_hash
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Load .env file if python-dotenv is available
# override=True ensures .env file values take precedence over existing env vars
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass  # python-dotenv not installed, will use environment variables only

# ----------------------------
# Paths
# ----------------------------
ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
ENROLL_DIR = ROOT / "enroll"
STATIC_DIR = ROOT / "static"

USERS_CSV = INPUT_DIR / "users.csv"     # first,last,email,password_hash,organizations_json,username,connected_apps_json
EMAILS_CSV = INPUT_DIR / "emails.csv"   # first,last,email (used by pipeline)
RESET_TOKENS_JSON = ROOT / "reset_tokens.json"  # email -> {token, expires}
MEETINGS_JSON = OUTPUT_DIR / "meetings.json"  # List of processed meetings with metadata
ORGANIZATIONS_JSON = ROOT / "organizations.json"  # Organizations and their members
ORGANIZATIONS_DIRECTORY_JSON = ROOT / "organizations_directory.json"  # Organization directory with details (name, abbrev, address, type, popularity)

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

# ----------------------------
# Helper functions
# ----------------------------
def ensure_dirs():
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    ENROLL_DIR.mkdir(parents=True, exist_ok=True)

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

def upload_to_connected_apps(user_email: str, pdf_path: Path, transcript_path: Path, meeting_name: str):
    """Upload meeting files to all connected cloud storage apps"""
    users = read_users()
    user = users.get(user_email.lower())
    if not user:
        return
    
    connected_apps = user.get("connected_apps", {})
    
    # Upload to Dropbox
    if "dropbox" in connected_apps:
        try:
            print(f"📤 Uploading {meeting_name} to Dropbox...")
            dropbox_config = connected_apps["dropbox"]
            access_token = decrypt_token(dropbox_config["access_token_encrypted"])
            refresh_token = None  # Dropbox doesn't use refresh tokens in standard OAuth
            token_expires_at = dropbox_config.get("token_expires_at")
            
            upload_to_dropbox(
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
        except Exception as e:
            error_msg = str(e)
            if "expired" in error_msg.lower() or "must reconnect" in error_msg.lower() or "reconnect" in error_msg.lower():
                # Mark Dropbox as needs reauth
                users[user_email.lower()]["connected_apps"]["dropbox"]["needs_reauth"] = True
                write_users(users)
                print(f"❌ Dropbox upload failed: {error_msg}. Marked connection as needs reconnect.")
            elif "not configured" in error_msg.lower():
                print(f"❌ Dropbox upload failed: {error_msg}")
            else:
                print(f"❌ Error uploading to Dropbox: {e}")
            # Don't raise - continue with other uploads
    
    # Upload to Google Drive
    if "googledrive" in connected_apps:
        try:
            print(f"📤 Uploading {meeting_name} to Google Drive...")
            upload_to_googledrive(
                access_token=decrypt_token(connected_apps["googledrive"]["access_token_encrypted"]),
                refresh_token=decrypt_token(connected_apps["googledrive"]["refresh_token_encrypted"]) if connected_apps["googledrive"].get("refresh_token_encrypted") else None,
                folder_name=connected_apps["googledrive"].get("folder_name", "PhiAI Meetings"),
                pdf_path=pdf_path,
                transcript_path=transcript_path,
                meeting_name=meeting_name
            )
            print(f"✅ Successfully uploaded {meeting_name} to Google Drive")
        except Exception as e:
            error_msg = str(e)
            if "expired" in error_msg.lower() or "invalid" in error_msg.lower():
                print(f"❌ Google Drive token expired or invalid. User needs to reconnect Google Drive in account settings.")
            else:
                print(f"❌ Error uploading to Google Drive: {e}")
    
    # Upload to Box
    if "box" in connected_apps:
        try:
            print(f"📤 Uploading {meeting_name} to Box...")
            box_config = connected_apps["box"]
            access_token = decrypt_token(box_config["access_token_encrypted"])
            refresh_token = decrypt_token(box_config["refresh_token_encrypted"]) if box_config.get("refresh_token_encrypted") else None
            token_expires_at = box_config.get("token_expires_at")
            
            upload_to_box(
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=token_expires_at,
                user_email=user_email,
                folder_name=box_config.get("folder_name", "PhiAI Meetings"),
                pdf_path=pdf_path,
                transcript_path=transcript_path,
                meeting_name=meeting_name
            )
            print(f"✅ Successfully uploaded {meeting_name} to Box")
        except Exception as e:
            error_msg = str(e)
            if "not installed" in error_msg.lower() or "ImportError" in error_msg:
                print(f"❌ Box SDK not installed. Install with: pip install boxsdk")
            elif "must reconnect" in error_msg.lower() or "reconnect" in error_msg.lower():
                # Mark Box as needs reauth
                users[user_email.lower()]["connected_apps"]["box"]["needs_reauth"] = True
                write_users(users)
                print(f"❌ Box upload failed: {error_msg}. Marked connection as needs reconnect.")
            elif "not configured" in error_msg.lower():
                print(f"❌ Box upload failed: {error_msg}")
            else:
                print(f"❌ Error uploading to Box: {e}")
            # Don't raise - continue with other uploads

def refresh_dropbox_token_if_needed(user_email: str, access_token: str, token_expires_at: int | None) -> tuple[str, int] | None:
    """
    Check if Dropbox token needs refresh and attempt refresh if needed.
    
    Note: Dropbox SDK automatically refreshes tokens when app_key/app_secret are provided,
    but only if token hasn't fully expired. If fully expired, user must reconnect.
    
    Returns:
        Tuple of (new_access_token, new_expires_at) if refresh succeeded, None if not needed or failed
    """
    if not token_expires_at:
        # No expiration stored - assume it's valid (SDK will handle refresh)
        return None
    
    current_time = int(time.time())
    time_until_expiry = token_expires_at - current_time
    
    # Only attempt refresh if token expires within 2 minutes
    if time_until_expiry > 120:
        # Token is still valid for more than 2 minutes
        return None
    
    # Token expires soon or already expired - Dropbox SDK with app_key/app_secret will auto-refresh
    # if token hasn't fully expired yet. If fully expired, SDK can't refresh and user must reconnect.
    # We can't manually refresh Dropbox tokens - the SDK handles it internally when provided with app_key/app_secret.
    print(f"[INFO] Dropbox token expires in {time_until_expiry}s - SDK will handle refresh if possible")
    return None  # SDK handles refresh automatically


def upload_to_dropbox(access_token: str, refresh_token: str | None, token_expires_at: int | None, user_email: str, folder_path: str, pdf_path: Path, transcript_path: Path, meeting_name: str):
    """Upload files to Dropbox with automatic token refresh via SDK"""
    try:
        import dropbox
        from dropbox.exceptions import AuthError
        
        DROPBOX_CLIENT_ID = os.getenv("DROPBOX_CLIENT_ID")
        DROPBOX_CLIENT_SECRET = os.getenv("DROPBOX_CLIENT_SECRET")
        
        if not DROPBOX_CLIENT_ID or not DROPBOX_CLIENT_SECRET:
            raise Exception("Dropbox credentials (DROPBOX_CLIENT_ID, DROPBOX_CLIENT_SECRET) not configured in .env file. Add these to your .env file.")
        
        # Check if token needs attention (expiring soon)
        current_time = int(time.time())
        if token_expires_at:
            time_until_expiry = token_expires_at - current_time
            if time_until_expiry < 120:  # Less than 2 minutes remaining
                print(f"[INFO] Dropbox token expires soon (in {time_until_expiry}s) - SDK will auto-refresh if possible")
        
        # Use oauth2_access_token parameter and provide app credentials
        # Dropbox SDK will automatically refresh token if needed (when app_key/app_secret provided)
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
            # Token expired or invalid - SDK attempted refresh but failed (token fully expired)
            error_msg = str(auth_err)
            print(f"[ERROR] Dropbox authentication failed: {error_msg}")
            
            # Dropbox SDK with app_key/app_secret should auto-refresh, but if token is fully expired,
            # SDK cannot refresh and user must reconnect
            if "expired" in error_msg.lower() or "expired_access_token" in error_msg.lower() or "invalid_access_token" in error_msg.lower():
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
        
        # Upload PDF (meeting report) - ONLY PDFs, NO TXT FILES
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            with open(pdf_path, 'rb') as f:
                file_data = f.read()
                dbx.files_upload(
                    file_data,
                    f"{meeting_folder_path}/{meeting_name}_meeting_report.pdf",
                    mode=dropbox.files.WriteMode.overwrite
                )
                print(f"  ✓ Uploaded PDF to Dropbox: {meeting_folder_path}/{meeting_name}_meeting_report.pdf ({len(file_data)} bytes)")
        else:
            print(f"  ⚠️  PDF not found or empty at {pdf_path}, skipping PDF upload to Dropbox")
        
        # NO TXT FILES - PDFs only
    except AuthError as e:
        error_msg = str(e)
        if "expired" in error_msg.lower() or "expired_access_token" in error_msg.lower() or "invalid_access_token" in error_msg.lower():
            raise Exception("Dropbox access token is expired and cannot be refreshed. User must reconnect Dropbox in account settings.")
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

def upload_to_googledrive(access_token: str, refresh_token: str, folder_name: str, pdf_path: Path, transcript_path: Path, meeting_name: str):
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
        
        service = build('drive', 'v3', credentials=creds)
        
        # Find or create folder
        folder_id = None
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive').execute()
        items = results.get('files', [])
        
        if items:
            folder_id = items[0]['id']
        else:
            # Create folder
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
        
        # Format meeting name for cleaner Google Drive naming
        clean_meeting_name = format_meeting_name_for_drive(meeting_name)
        
        # Upload PDF (meeting report)
        # Upload PDF (meeting report) - ONLY PDFs, NO TXT FILES
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            file_metadata = {'name': f'{clean_meeting_name}_meeting_report.pdf', 'parents': [folder_id]}
            media = MediaFileUpload(str(pdf_path), mimetype='application/pdf')
            file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            print(f"  ✓ Uploaded PDF to Google Drive: {folder_name}/{clean_meeting_name}_meeting_report.pdf (ID: {file.get('id')})")
        else:
            print(f"  ⚠️  PDF not found or empty at {pdf_path}, skipping PDF upload to Google Drive")
        
        # NO TXT FILES - PDFs only
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
    """Upload files to Box with automatic token refresh"""
    try:
        try:
            from boxsdk import Client, OAuth2  # type: ignore
            from boxsdk.exception import BoxAPIException  # type: ignore
        except ImportError as import_err:
            error_msg = f"boxsdk package not installed. Install with: pip install boxsdk. Import error: {import_err}"
            print(f"[ERROR] Box import error: {error_msg}")
            raise Exception(error_msg)
        
        BOX_CLIENT_ID = os.getenv("BOX_CLIENT_ID")
        BOX_CLIENT_SECRET = os.getenv("BOX_CLIENT_SECRET")
        
        if not BOX_CLIENT_ID or not BOX_CLIENT_SECRET:
            raise Exception("Box credentials (BOX_CLIENT_ID, BOX_CLIENT_SECRET) not configured in .env file")
        
        # Check if token needs refresh (within 2 minutes of expiration)
        current_time = int(time.time())
        if token_expires_at and (token_expires_at - current_time) < 120:  # Less than 2 minutes remaining
            print(f"[INFO] Box token expires soon (in {token_expires_at - current_time}s), refreshing...")
            refresh_result = refresh_box_token(user_email, refresh_token)
            if refresh_result:
                access_token, refresh_token, token_expires_at = refresh_result
                print(f"[SUCCESS] Box token refreshed")
            else:
                raise Exception("Missing refresh_token or app credentials. User must reconnect Box.")
        
        # Create OAuth2 object with refresh callback for auto-refresh
        oauth = OAuth2(
            client_id=BOX_CLIENT_ID,
            client_secret=BOX_CLIENT_SECRET,
            access_token=access_token,
            refresh_token=refresh_token
        )
        
        # Set up refresh callback to update stored tokens
        def store_tokens(access_token_new, refresh_token_new):
            """Callback to store refreshed tokens"""
            try:
                users = read_users()
                if user_email.lower() in users:
                    if "connected_apps" not in users[user_email.lower()]:
                        users[user_email.lower()]["connected_apps"] = {}
                    if "box" not in users[user_email.lower()]["connected_apps"]:
                        users[user_email.lower()]["connected_apps"]["box"] = {}
                    
                    users[user_email.lower()]["connected_apps"]["box"]["access_token_encrypted"] = encrypt_token(access_token_new)
                    if refresh_token_new:
                        users[user_email.lower()]["connected_apps"]["box"]["refresh_token_encrypted"] = encrypt_token(refresh_token_new)
                    # Update expiration (default 1 hour)
                    expires_at = int(time.time()) + 3600 - 120
                    users[user_email.lower()]["connected_apps"]["box"]["token_expires_at"] = expires_at
                    write_users(users)
                    print(f"[SUCCESS] Box tokens updated after SDK auto-refresh")
            except Exception as e:
                print(f"[WARN] Failed to store refreshed Box tokens: {e}")
        
        oauth.refresh = store_tokens
        
        client = Client(oauth)
        
        # Test connection by getting current user
        try:
            client.user(user_id='me').get()
            print(f"[SUCCESS] Box connection verified")
        except BoxAPIException as api_err:
            error_msg = str(api_err)
            if api_err.status == 401:
                # Token expired - try refresh
                if refresh_token:
                    print(f"[INFO] Box token expired (401), attempting refresh...")
                    refresh_result = refresh_box_token(user_email, refresh_token)
                    if refresh_result:
                        access_token, refresh_token, token_expires_at = refresh_result
                        # Retry with new token
                        oauth = OAuth2(
                            client_id=BOX_CLIENT_ID,
                            client_secret=BOX_CLIENT_SECRET,
                            access_token=access_token,
                            refresh_token=refresh_token
                        )
                        oauth.refresh = store_tokens
                        client = Client(oauth)
                        client.user(user_id='me').get()  # Test again
                    else:
                        raise Exception("Missing refresh_token or app credentials. User must reconnect Box.")
                else:
                    raise Exception("Box access token is expired and no refresh token available. User must reconnect Box.")
            else:
                raise Exception(f"Box API error: {error_msg}")
        
        # Format meeting name for folder structure (match Google Drive: YYYY/MM/DD)
        formatted_meeting_name = format_meeting_name_for_drive(meeting_name)
        
        # Build folder structure: PhiAI Meetings/meeting YYYY/MM/DD/
        base_folder_name = folder_name
        meeting_folder_path = f"{base_folder_name}/meeting {formatted_meeting_name}"
        
        # Get root folder
        root_folder = client.folder('0')
        
        # Find or create nested folder structure
        current_folder = root_folder
        folder_parts = meeting_folder_path.split('/')
        
        for folder_part in folder_parts:
            if not folder_part.strip():
                continue
            
            # Look for folder in current location
            folder_id = None
            try:
                items = current_folder.get_items()
                for item in items:
                    if item.type == 'folder' and item.name == folder_part:
                        folder_id = item.id
                        break
                
                if folder_id:
                    current_folder = client.folder(folder_id)
                else:
                    # Create folder
                    new_folder = current_folder.create_subfolder(folder_part)
                    current_folder = new_folder
                    print(f"  [INFO] Created Box folder: {folder_part}")
            except BoxAPIException as e:
                if e.status == 409:  # Conflict - folder already exists (race condition)
                    # Find it again
                    items = current_folder.get_items()
                    for item in items:
                        if item.type == 'folder' and item.name == folder_part:
                            current_folder = client.folder(item.id)
                            break
                    else:
                        raise Exception(f"Failed to find or create Box folder {folder_part}: {e}")
                else:
                    raise Exception(f"Failed to create Box folder {folder_part}: {e}")
        
        # Upload PDF (meeting report) - ONLY PDFs, NO TXT FILES
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            file_size = pdf_path.stat().st_size
            # Check if file already exists and overwrite/version it
            try:
                existing_files = list(current_folder.get_items())
                existing_file_id = None
                for item in existing_files:
                    if item.type == 'file' and item.name == f'{meeting_name}_meeting_report.pdf':
                        existing_file_id = item.id
                        break
                
                if existing_file_id:
                    # Upload new version
                    with open(pdf_path, 'rb') as f:
                        file = client.file(existing_file_id).update_contents_with_stream(
                            f,
                            etag=None  # Force new version
                        )
                    print(f"  ✓ Uploaded PDF to Box (new version): {meeting_folder_path}/{meeting_name}_meeting_report.pdf (ID: {file.id}, {file_size} bytes)")
                else:
                    # Upload new file
                    with open(pdf_path, 'rb') as f:
                        file = current_folder.upload_stream(f, f'{meeting_name}_meeting_report.pdf')
                    print(f"  ✓ Uploaded PDF to Box: {meeting_folder_path}/{meeting_name}_meeting_report.pdf (ID: {file.id}, {file_size} bytes)")
            except Exception as upload_err:
                raise Exception(f"Failed to upload PDF to Box: {upload_err}")
        else:
            print(f"  ⚠️  PDF not found or empty at {pdf_path}, skipping PDF upload to Box")
        
        # NO TXT FILES - PDFs only
    except BoxAPIException as e:
        error_msg = str(e)
        if e.status == 401:
            raise Exception("Box access token is expired and cannot be refreshed. User must reconnect Box in account settings.")
        else:
            raise Exception(f"Box API error: {error_msg}")
    except Exception as e:
        print(f"[ERROR] Box upload error: {e}")
        import traceback
        if os.getenv("FLASK_DEBUG") == "1":
            traceback.print_exc()
        raise

def run_pipeline(audio_path: Path, cfg: dict, participants: list = None):
    """Run the transcription pipeline with optional participant list for email sending."""
    PY = sys.executable
    stem = audio_path.stem
    meeting_name = stem  # Default to stem
    
    # Try to extract meeting name from filename (format: name_TIMESTAMP)
    if "_" in stem:
        parts = stem.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():  # Has timestamp suffix
            meeting_name = parts[0].replace("_", " ")

    cmd1 = [PY, "transcribe_assemblyai.py", str(audio_path)]
    if cfg.get("speakers_expected") is not None:
        cmd1 += ["--speakers", str(cfg["speakers_expected"])]

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

    # Run transcription and speaker identification
    for cmd in (cmd1, cmd2):
        rc = run_cmd(cmd)
        if rc != 0:
            print(f"\n❌ Pipeline stopped (exit {rc})")
            return

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
        
        meeting_data = {
            "id": stem,
            "name": meeting_name,
            "original_filename": audio_path.name,
            "processed_at": datetime.now().isoformat(),
            "audio_path": str(audio_path.relative_to(ROOT)) if audio_path.exists() else None,
            "transcript_path": str(transcript_path.relative_to(ROOT)) if transcript_exists else None,
            "pdf_path": str(pdf_path.relative_to(ROOT)) if pdf_exists else None,
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
        
        # Upload to connected apps for all participants
        # ONLY upload PDFs - NO TXT FILES
        if pdf_exists and pdf_path.exists() and pdf_path.stat().st_size > 0:
            try:
                # Get all participant emails
                participant_emails_list = []
                if participants:
                    for p in participants:
                        if isinstance(p, str):
                            participant_emails_list.append(p.lower())
                        elif isinstance(p, dict) and "email" in p:
                            participant_emails_list.append(p["email"].lower())
                
                # Also include the uploader
                uploader_email = cfg.get("uploader_email")
                if uploader_email and uploader_email.lower() not in participant_emails_list:
                    participant_emails_list.append(uploader_email.lower())
                
                # Upload to each participant's connected apps
                # Pass pdf_path and transcript_path - upload functions will check if files exist
                print(f"📤 Preparing to upload meeting files to connected apps...")
                print(f"   PDF path: {pdf_path}")
                print(f"   PDF exists: {pdf_path.exists()}")
                print(f"   PDF size: {pdf_path.stat().st_size if pdf_path.exists() else 0} bytes")
                print(f"   Transcript path: {transcript_path}")
                print(f"   Transcript exists: {transcript_path.exists()}")
                
                for email in participant_emails_list:
                    print(f"📤 Uploading meeting files to connected apps for {email}...")
                    upload_to_connected_apps(email, pdf_path, transcript_path, meeting_name)
            except Exception as e:
                print(f"⚠️  Warning: Could not upload to connected apps: {e}")
                import traceback
                traceback.print_exc()
        
    except Exception as e:
        print(f"\n⚠️  Warning: Could not save meeting metadata: {e}")

    print(f"\n✅ Completed pipeline for: {audio_path.name}\n")

# Initialize
ensure_dirs()
init_users_csv()

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
    meetings = get_user_meetings(user["email"])
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
    return render_template("signup.html", org_types=ORGANIZATION_TYPES, organizations_directory=organizations_directory)

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

    if not first or not last:
        flash("First and last name required.")
        return redirect(url_for("signup_get"))
    if email != email2 or not valid_email(email):
        flash("Emails must match and be valid.")
        return redirect(url_for("signup_get"))
    if pw != pw2 or len(pw) < 8:
        flash("Passwords must match and be at least 8 characters.")
        return redirect(url_for("signup_get"))
    
    # Validate username
    valid, username_result = validate_username(username)
    if not valid:
        flash(username_result)
        return redirect(url_for("signup_get"))
    username = username_result

    users = read_users()
    if email in users:
        flash("That email is already registered. Use Edit Account / Login.")
        return redirect(url_for("login_get"))

    # Check username uniqueness (case-insensitive)
    for existing_user in users.values():
        if existing_user.get("username", "").lower() == username.lower():
            flash("That username is already taken. Please choose another.")
            return redirect(url_for("signup_get"))

    # Parse organizations from form - NOW REQUIRED
    organizations = []
    org_count = int(request.form.get("org_count", "0") or "0")
    
    # Require at least one organization
    if org_count == 0:
        flash("You must add at least one organization.")
        return redirect(url_for("signup_get"))
    
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
            # Add to organizations.json (type is optional, default to "other")
            add_user_to_organization(org_name, org_type or "other", email)

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

    session["user_email"] = email
    session.permanent = True
    flash("Account created. Now record/upload your enrollment audio.")
    return redirect(url_for("enroll_get"))

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
    
    # Only show recordings that match this user's "firstname,lastname" or "username" format
    first = user.get("first", "").strip().lower()
    last = user.get("last", "").strip().lower()
    user_prefix_comma = f"{first},{last}".lower() if first and last else ""
    user_prefix_username = username.lower()
    
    if enroll_dir.exists():
        for f in sorted(enroll_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                # Only include files that match user's firstname,lastname OR username format
                filename_lower = f.name.lower()
                # Check if filename starts with firstname,lastname or username (handles both formats)
                matches_user = False
                if user_prefix_comma and filename_lower.startswith(user_prefix_comma):
                    matches_user = True
                elif filename_lower.startswith(user_prefix_username):
                    matches_user = True
                
                if matches_user and f.stat().st_size > 0:
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
    
    # Check if user has enrollment audio - must be >= 30 seconds
    username = user.get("username", "").strip().lower()
    has_enrollment = False
    if username:
        enroll_dir = ENROLL_DIR
        if enroll_dir.exists():
            for f in enroll_dir.iterdir():
                if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                    if enrollment_file_matches_user(f.name, user) and f.stat().st_size > 0:
                        duration = get_audio_duration(f)
                        if duration >= 30.0:
                            has_enrollment = True
                            break
    
    organizations_directory = load_organizations_directory()
    return render_template("record_meeting.html", user=user, org_types=ORGANIZATION_TYPES, organizations_directory=organizations_directory, has_enrollment=has_enrollment)

@app.post("/upload_meeting")
def upload_meeting():
    """Handle meeting audio upload and trigger pipeline"""
    if not require_login():
        return ("Not logged in", 401), 401
    
    user = current_user()
    if not user:
        return ("User not found", 400), 400
    
    # Check if user has enrollment audio - must be >= 30 seconds
    username = user.get("username", "").strip().lower()
    has_enrollment = False
    if username:
        enroll_dir = ENROLL_DIR
        if enroll_dir.exists():
            for f in enroll_dir.iterdir():
                if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                    if enrollment_file_matches_user(f.name, user) and f.stat().st_size > 0:
                        duration = get_audio_duration(f)
                        if duration >= 30.0:
                            has_enrollment = True
                            break
    
    if not has_enrollment:
        return jsonify({"error": "enrollment_required", "message": "You must have enrollment audio (at least 30 seconds) to record a meeting. Please record your enrollment audio first."}), 400
    
    f = request.files.get("audio")
    if not f:
        return ("Missing audio", 400), 400
    
    filename = (f.filename or "audio").strip()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        return (f"Unsupported file type: {ext}", 400), 400
    
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
    
    # Store upload timestamp for date logic
    upload_timestamp = datetime.now().isoformat()
    
    # Trigger pipeline in background thread with participants
    cfg = load_config()
    # Add uploader email to config so it can be used for cloud uploads
    cfg["uploader_email"] = user_email
    cfg["upload_timestamp"] = upload_timestamp  # Store upload timestamp for PDF date
    cfg["source_organizations"] = source_organizations  # Store source organizations for PDF header
    threading.Thread(
        target=run_pipeline, 
        args=(meeting_path, cfg, participants),
        daemon=True
    ).start()
    
    return jsonify({
        "status": "processing",
        "filename": meeting_path.name,
        "redirect": url_for("upload_success"),
        "message": "Meeting uploaded successfully. Processing will begin shortly. You and all participants will receive an email when transcription is complete."
    }), 202

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
    """Serve meeting transcript file"""
    if not require_login():
        return ("Not logged in", 401)
    transcript_path = OUTPUT_DIR / f"{meeting_id}_named_script.txt"
    if transcript_path.exists():
        return send_from_directory(OUTPUT_DIR, f"{meeting_id}_named_script.txt")
    return ("Transcript not found", 404)

@app.get("/meeting/<meeting_id>/audio")
def meeting_audio(meeting_id: str):
    """Serve meeting audio file"""
    if not require_login():
        return ("Not logged in", 401)
    # Check meetings.json to find the original audio file
    meetings = load_meetings()
    meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
    if meeting and meeting.get("audio_path"):
        audio_path = ROOT / meeting["audio_path"]
        if audio_path.exists():
            return send_from_directory(audio_path.parent, audio_path.name)
    # Fallback: try to find in input directory
    for ext in [".mp4", ".m4a", ".wav", ".mp3", ".mov"]:
        audio_path = INPUT_DIR / f"{meeting_id}{ext}"
        if audio_path.exists():
            return send_from_directory(INPUT_DIR, audio_path.name)
    return ("Audio file not found", 404)

@app.get("/meeting/<meeting_id>/pdf")
def meeting_pdf(meeting_id: str):
    """Serve meeting PDF transcript"""
    if not require_login():
        return ("Not logged in", 401)
    # Try meeting report first, fallback to old transcript PDF for backward compatibility
    pdf_path = OUTPUT_DIR / f"{meeting_id}_meeting_report.pdf"
    if pdf_path.exists():
        return send_from_directory(OUTPUT_DIR, f"{meeting_id}_meeting_report.pdf")
    # Fallback to old format
    pdf_path = OUTPUT_DIR / f"{meeting_id}_transcript.pdf"
    if pdf_path.exists():
        return send_from_directory(OUTPUT_DIR, f"{meeting_id}_transcript.pdf")
    return ("PDF not found", 404)

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

@app.post("/account/delete")
def delete_account():
    """Delete user account and all associated data"""
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401
    
    user = current_user()
    user_email = user["email"].lower()
    username = user.get("username", "").strip().lower()
    first = user.get("first", "").strip()
    last = user.get("last", "").strip()
    
    deleted_files = []
    errors = []
    
    # 1. Remove user from all organizations
    orgs = load_organizations()
    for org_name in list(orgs.keys()):
        if user_email in [m.lower() for m in orgs[org_name].get("members", [])]:
            remove_user_from_organization(org_name, user_email)
    
    # 2. Delete ALL enrollment audio files (using proper matching function)
    # This handles both firstname,lastname.ext and username.ext formats
    enroll_dir = ENROLL_DIR
    if enroll_dir.exists():
        for f in list(enroll_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_UPLOAD_EXT:
                if enrollment_file_matches_user(f.name, user):
                    try:
                        f.unlink()
                        deleted_files.append(f"enrollment: {f.name}")
                        print(f"Deleted enrollment file: {f.name}")
                    except Exception as e:
                        error_msg = f"Could not delete enrollment file {f.name}: {e}"
                        errors.append(error_msg)
                        print(f"Warning: {error_msg}")
    
    # 3. Remove user from meetings (remove from participant lists)
    # Also delete meeting output files if user was the only participant
    meetings = load_meetings()
    updated_meetings = []
    meetings_to_delete = []
    
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
        
        # If no participants left, mark meeting for deletion
        if not updated_participants:
            meetings_to_delete.append(meeting)
        else:
            meeting["participants"] = updated_participants
            updated_meetings.append(meeting)
    
    # Delete meetings with no participants left
    for meeting in meetings_to_delete:
        # Delete meeting output files
        meeting_stem = meeting.get("id") or meeting.get("stem", "")
        if meeting_stem:
            try:
                # Delete transcript files
                transcript_pdf = OUTPUT_DIR / f"{meeting_stem}_transcript.pdf"
                transcript_txt = OUTPUT_DIR / f"{meeting_stem}_transcript.txt"
                utterances_json = OUTPUT_DIR / f"{meeting_stem}_utterances.json"
                named_script = OUTPUT_DIR / f"{meeting_stem}_named_script.txt"
                
                for file_path in [transcript_pdf, transcript_txt, utterances_json, named_script]:
                    if file_path.exists():
                        file_path.unlink()
                        deleted_files.append(f"meeting: {file_path.name}")
                        print(f"Deleted meeting file: {file_path.name}")
            except Exception as e:
                error_msg = f"Could not delete meeting files for {meeting_stem}: {e}"
                errors.append(error_msg)
                print(f"Warning: {error_msg}")
    
    # Update meetings.json (only with meetings that still have participants)
    MEETINGS_JSON.write_text(json.dumps(updated_meetings, indent=2), encoding="utf-8")
    
    # 4. Delete user from users.csv
    # This removes username, password, email, and all user data
    # User can recreate account with same email since we're deleting the entry
    users = read_users()
    if user_email in users:
        del users[user_email]
        write_users(users)
        print(f"Deleted user account: {user_email}")
    
    # 5. Clear session (prevents any further access)
    session.clear()
    
    # Log summary
    print(f"Account deletion summary for {user_email}:")
    print(f"  - Deleted {len(deleted_files)} files")
    if errors:
        print(f"  - {len(errors)} errors occurred")
        for err in errors:
            print(f"    {err}")
    
    return jsonify({
        "status": "deleted", 
        "message": "Account deleted successfully",
        "deleted_files_count": len(deleted_files),
        "errors": errors if errors else None
    }), 200

@app.get("/account/connect_apps")
def connect_apps_get():
    """Connect Apps page"""
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    
    # Get connected apps
    connected_apps = user.get("connected_apps", {})
    
    return render_template("connect_apps.html", user=user, connected_apps=connected_apps)

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
    
    dropbox_auth_url = (
        "https://www.dropbox.com/oauth2/authorize?"
        f"client_id={DROPBOX_CLIENT_ID}&"
        f"response_type=code&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        f"scope={scope}"
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
        # Dropbox OAuth 2.0 doesn't use refresh tokens - tokens are refreshed by SDK using app_key/app_secret
        # However, we store expires_in to track when token needs refresh
        expires_in = token_data.get("expires_in", 14400)  # Default 4 hours (14400 seconds)
        
        if not access_token:
            print("[ERROR] No access token in Dropbox response")
            flash("Failed to get access token from Dropbox.")
            return redirect(url_for("connect_apps_get"))
        
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
        
        # Calculate expiration timestamp (expires_in is in seconds, subtract 2 min buffer)
        expires_at = int(time.time()) + expires_in - 120
        
        users[user["email"]]["connected_apps"]["dropbox"] = {
            "access_token_encrypted": encrypted_token,
            "token_expires_at": expires_at,  # Unix timestamp for expiration check
            "folder_path": "/PhiAI/Meetings",
            "connected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
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
        flash(f"Box authorization failed: {error}")
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
        
        user = current_user()
        users = read_users()
        
        if "connected_apps" not in users[user["email"]]:
            users[user["email"]]["connected_apps"] = {}
        
        encrypted_token = encrypt_token(access_token)
        encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None
        
        # Calculate expiration timestamp (expires_in is in seconds, subtract 2 min buffer)
        expires_at = int(time.time()) + expires_in - 120
        
        users[user["email"]]["connected_apps"]["box"] = {
            "access_token_encrypted": encrypted_token,
            "refresh_token_encrypted": encrypted_refresh,
            "token_expires_at": expires_at,  # Unix timestamp for expiration check
            "folder_name": "PhiAI Meetings",
            "connected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        }
        
        write_users(users)
        print(f"[SUCCESS] Box connected for {user['email']}, token expires in {expires_in}s (at {expires_at})")
        flash("Box connected successfully!")
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

if __name__ == "__main__":
    ensure_dirs()
    app.run(debug=True, host="127.0.0.1", port=5000)

