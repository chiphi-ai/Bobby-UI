import csv
import os
import re
import shutil
import sys
import time
import json
import subprocess
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ----------------------------
# Paths / Defaults
# ----------------------------
ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
ENROLL_DIR = ROOT / "enroll"

DB_CSV = INPUT_DIR / "emails.csv"
CONFIG_PATH = ROOT / "config.json"

ALLOWED_EXT = {".m4a", ".wav", ".mp3", ".mp4", ".mov", ".aac", ".flac", ".ogg"}

DEFAULT_CONFIG = {
    "watch_dir": r"C:\Users\bjones25\Box\Meetings\Drop",
    "speakers_expected": None,         # None = auto-detect
    "stable_seconds": 6,
    "stable_checks": 4,
    "min_speaker_seconds": 6.0,        # threshold to count as "spoke"
    "min_speaker_words": 20,           # threshold to count as "spoke"
}


# ----------------------------
# Utils
# ----------------------------
def ensure_dirs():
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ENROLL_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def norm_name(first: str, last: str) -> str:
    first = (first or "").strip().lower()
    last = (last or "").strip().lower()
    first = re.sub(r"\s+", "", first)
    last = re.sub(r"\s+", "", last)
    return f"{first},{last}"

def valid_email(email: str) -> bool:
    email = (email or "").strip()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))

def prompt(msg: str) -> str:
    return input(msg).strip()

def press_enter():
    input("\nPress Enter to continue...")

def which_python():
    return sys.executable

def run_cmd(cmd: list[str]) -> int:
    print("\n> " + " ".join(map(str, cmd)))
    return subprocess.run(cmd).returncode

def is_temporary_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.endswith(".tmp")
        or name.endswith(".part")
        or name.startswith("~$")
        or name.startswith(".")
        or ".partial" in name
    )

def file_is_stable(path: Path, stable_seconds: int, checks: int) -> bool:
    last = -1
    for _ in range(checks):
        if not path.exists():
            return False
        size = path.stat().st_size
        if size != last:
            last = size
        time.sleep(stable_seconds)
    # final check
    return path.exists() and path.stat().st_size == last


# ----------------------------
# Database (CSV) first,last,email
# ----------------------------
def init_db_if_missing():
    if not DB_CSV.exists():
        DB_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(DB_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["first", "last", "email"])
            w.writeheader()

def read_db() -> dict:
    init_db_if_missing()
    people = {}
    with open(DB_CSV, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            first = (row.get("first") or "").strip()
            last = (row.get("last") or "").strip()
            email = (row.get("email") or "").strip()
            if not first or not last or not email:
                continue
            key = norm_name(first, last)
            people[key] = {"first": first, "last": last, "email": email}
    return people

def write_db(people: dict):
    init_db_if_missing()
    rows = sorted(people.values(), key=lambda x: (x["last"].lower(), x["first"].lower()))
    with open(DB_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["first", "last", "email"])
        w.writeheader()
        for row in rows:
            w.writerow(row)

def add_or_edit_person():
    ensure_dirs()
    people = read_db()

    first = prompt("First name: ")
    last = prompt("Last name: ")
    if not first or not last:
        print("Need both first and last.")
        return

    key = norm_name(first, last)

    email = prompt("Email: ")
    email2 = prompt("Confirm email: ")
    if email != email2:
        print("Emails do not match.")
        return
    if not valid_email(email):
        print("That email format looks invalid.")
        return

    existed = key in people
    people[key] = {"first": first.strip(), "last": last.strip(), "email": email.strip()}
    write_db(people)

    print(f"\n✅ {'Updated' if existed else 'Added'}: {first} {last} <{email}>")
    print(f"DB: {DB_CSV}")

    # Optional enrollment audio
    add_audio = prompt("\nAdd/update enrollment audio clip now? (y/n): ").lower()
    if add_audio == "y":
        p = prompt("Path to audio file (e.g. C:\\path\\clip.m4a): ")
        src = Path(p.strip('"'))
        if not src.exists():
            print("File not found.")
            return
        ext = src.suffix.lower()
        if ext not in ALLOWED_EXT:
            print(f"Unsupported audio type: {ext}")
            return

        dest = ENROLL_DIR / f"{key}{ext}"
        shutil.copy2(src, dest)
        print(f"✅ Saved enrollment clip: {dest}")

def list_people():
    people = read_db()
    if not people:
        print("No people in DB yet.")
        return
    print("\nPeople in database:")
    for k, v in sorted(people.items(), key=lambda kv: (kv[1]["last"].lower(), kv[1]["first"].lower())):
        print(f" - {v['first']} {v['last']}  <{v['email']}>   key={k}")

def remove_person():
    people = read_db()
    first = prompt("First name to remove: ")
    last = prompt("Last name to remove: ")
    key = norm_name(first, last)
    if key not in people:
        print("Not found.")
        return
    v = people.pop(key)
    write_db(people)
    print(f"✅ Removed {v['first']} {v['last']} from DB.")

    # Optional remove enroll file(s)
    for f in ENROLL_DIR.glob(f"{key}.*"):
        try:
            f.unlink()
            print(f"Removed enroll file: {f.name}")
        except Exception:
            pass


# ----------------------------
# Pipeline runner
# ----------------------------
def run_meeting_pipeline(audio_path: Path, cfg: dict):
    """
    Runs:
      1) transcribe_assemblyai.py
      2) identify_speakers.py
      3) email_named_script.py (filters to speakers who spoke using thresholds)
    """
    PY = which_python()
    stem = audio_path.stem

    # 1) transcription (allow auto-detect if speakers_expected is None)
    cmd1 = [PY, "transcribe_assemblyai.py", str(audio_path)]
    if cfg.get("speakers_expected") is not None:
        cmd1 += ["--speakers", str(cfg["speakers_expected"])]

    # 2) speaker identification
    cmd2 = [
        PY, "identify_speakers.py",
        f"output\\{stem}_utterances.json",
        "enroll",
        f"output\\{stem}_named_script.txt"
    ]

    # 3) email only speakers who spoke
    # We'll pass thresholds into the email script.
    cmd3 = [
        PY, "email_named_script.py",
        "--stem", stem,
        "--min-seconds", str(cfg.get("min_speaker_seconds", 6.0)),
        "--min-words", str(cfg.get("min_speaker_words", 20)),
    ]

    for cmd in (cmd1, cmd2, cmd3):
        rc = run_cmd(cmd)
        if rc != 0:
            print(f"\n❌ Pipeline stopped (exit {rc}).")
            return

    print(f"\n✅ Completed pipeline for: {audio_path.name}")


# ----------------------------
# Watcher
# ----------------------------
class Handler(FileSystemEventHandler):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)

        if path.suffix.lower() not in ALLOWED_EXT:
            return
        if is_temporary_file(path):
            return

        print(f"\n📥 New file detected: {path.name}")
        print("   Waiting for Box sync to finish...")

        stable_seconds = int(self.cfg.get("stable_seconds", 6))
        stable_checks = int(self.cfg.get("stable_checks", 4))

        if file_is_stable(path, stable_seconds=stable_seconds, checks=stable_checks):
            print("   File stable. Running pipeline.")
            run_meeting_pipeline(path, self.cfg)
            print("\n👀 Still watching for the next file...")
        else:
            print("   File never became stable (moved/renamed/deleted). Skipping.")


def run_watcher_forever(cfg: dict):
    ensure_dirs()
    watch_dir = Path(cfg["watch_dir"])
    watch_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n👀 Watching folder:\n  {watch_dir.resolve()}")
    print("Leave this window open. Press Ctrl+C to stop.\n")

    observer = Observer()
    observer.schedule(Handler(cfg), str(watch_dir), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# ----------------------------
# Config menu
# ----------------------------
def set_watch_dir(cfg: dict):
    p = prompt(f"Current watch_dir:\n  {cfg['watch_dir']}\nNew watch_dir (paste full path): ")
    if p:
        cfg["watch_dir"] = p.strip('"')
        save_config(cfg)
        print("✅ Updated watch_dir saved.")

def set_thresholds(cfg: dict):
    s = prompt(f"Minimum seconds to count as 'spoke' (current {cfg['min_speaker_seconds']}): ")
    w = prompt(f"Minimum words to count as 'spoke' (current {cfg['min_speaker_words']}): ")
    try:
        if s:
            cfg["min_speaker_seconds"] = float(s)
        if w:
            cfg["min_speaker_words"] = int(w)
        save_config(cfg)
        print("✅ Thresholds saved.")
    except Exception:
        print("Invalid input.")

def set_speakers_expected(cfg: dict):
    v = prompt(f"Speakers expected (current {cfg['speakers_expected']}) — enter blank for auto-detect, or a number: ")
    if not v.strip():
        cfg["speakers_expected"] = None
    else:
        try:
            cfg["speakers_expected"] = int(v)
        except Exception:
            print("Invalid number.")
            return
    save_config(cfg)
    print("✅ speakers_expected saved.")

def main_menu():
    ensure_dirs()
    cfg = load_config()
    init_db_if_missing()

    while True:
        print("\n==================== DIO APP ====================")
        print("1) Start AUTO mode (watch folder + process every new meeting)")
        print("2) Add or edit a person (CSV + optional enroll audio)")
        print("3) List people in database")
        print("4) Remove a person")
        print("5) Settings: watch folder")
        print("6) Settings: thresholds (min seconds/words to email)")
        print("7) Settings: speakers_expected (blank = auto-detect)")
        print("0) Quit")
        choice = prompt("Choose: ")

        if choice == "1":
            run_watcher_forever(cfg)
        elif choice == "2":
            add_or_edit_person()
            press_enter()
        elif choice == "3":
            list_people()
            press_enter()
        elif choice == "4":
            remove_person()
            press_enter()
        elif choice == "5":
            set_watch_dir(cfg)
            press_enter()
        elif choice == "6":
            set_thresholds(cfg)
            press_enter()
        elif choice == "7":
            set_speakers_expected(cfg)
            press_enter()
        elif choice == "0":
            print("Bye.")
            return
        else:
            print("Invalid choice.")

if __name__ == "__main__":
    main_menu()
