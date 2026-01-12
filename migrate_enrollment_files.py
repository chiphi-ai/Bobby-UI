#!/usr/bin/env python3
"""Migrate enrollment files from username format to firstname,lastname format"""

import json
from pathlib import Path

ENROLL_DIR = Path("enroll")
USERS_CSV = Path("input/users.csv")

def read_users():
    """Read users from CSV"""
    users = {}
    if not USERS_CSV.exists():
        return users
    
    with open(USERS_CSV, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        if not lines:
            return users
        
        # Parse header
        header = lines[0].strip().split(',')
        email_idx = header.index('email') if 'email' in header else 0
        username_idx = header.index('username') if 'username' in header else -1
        first_idx = header.index('first') if 'first' in header else -1
        last_idx = header.index('last') if 'last' in header else -1
        
        # Parse rows
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = [p.strip().strip('"') for p in line.split(',')]
            if len(parts) <= email_idx:
                continue
            
            email = parts[email_idx].lower()
            user = {"email": email}
            if username_idx >= 0 and len(parts) > username_idx:
                user["username"] = parts[username_idx]
            if first_idx >= 0 and len(parts) > first_idx:
                user["first"] = parts[first_idx]
            if last_idx >= 0 and len(parts) > last_idx:
                user["last"] = parts[last_idx]
            
            users[email] = user
    
    return users

def main():
    if not ENROLL_DIR.exists():
        print(f"Enrollment directory {ENROLL_DIR} does not exist!")
        return
    
    users = read_users()
    print(f"Loaded {len(users)} users")
    
    # Build username -> firstname,lastname mapping
    username_map = {}
    for user in users.values():
        username = user.get("username", "").strip().lower()
        first = user.get("first", "").strip().lower()
        last = user.get("last", "").strip().lower()
        if username and first and last:
            username_map[username] = f"{first},{last}"
    
    print(f"Found {len(username_map)} username mappings")
    
    # Find and rename files
    renamed_count = 0
    skipped_count = 0
    
    for f in sorted(ENROLL_DIR.iterdir()):
        if not f.is_file():
            continue
        
        # Check if filename is in username format (no comma, before extension)
        stem = f.stem.lower()
        # Remove (2), (3), etc. to get base username
        base_name = stem
        if '(' in stem:
            base_name = stem.split('(')[0]
        
        # Check if this matches a username and we have a mapping
        if base_name in username_map:
            new_name = username_map[base_name]
            
            # Preserve the (2), (3) suffix if present
            if '(' in stem:
                suffix = stem[stem.index('('):]
                new_name = f"{new_name}{suffix}"
            else:
                suffix = ""
            
            # Keep the extension
            ext = f.suffix
            new_filename = f"{new_name}{ext}"
            new_path = ENROLL_DIR / new_filename
            
            # Skip if already in correct format or new file already exists
            if "," in f.name.lower():
                print(f"Skipping {f.name} (already in firstname,lastname format)")
                skipped_count += 1
                continue
            
            if new_path.exists():
                print(f"Skipping {f.name} -> {new_filename} (target already exists)")
                skipped_count += 1
                continue
            
            # Rename the file
            try:
                f.rename(new_path)
                print(f"Renamed: {f.name} -> {new_filename}")
                renamed_count += 1
            except Exception as e:
                print(f"Error renaming {f.name}: {e}")
                skipped_count += 1
        else:
            # Check if already in firstname,lastname format
            if "," in stem:
                print(f"Skipping {f.name} (already in firstname,lastname format)")
                skipped_count += 1
            else:
                print(f"Skipping {f.name} (no username mapping found)")
                skipped_count += 1
    
    print(f"\nMigration complete!")
    print(f"  Renamed: {renamed_count} files")
    print(f"  Skipped: {skipped_count} files")

if __name__ == "__main__":
    main()
