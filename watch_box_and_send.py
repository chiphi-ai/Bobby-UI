import time
import sys
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ✅ CHANGE THIS to your actual Box-synced folder
WATCH_DIR = Path(r"C:\Users\bjones25\Box\Meetings\Drop")

ALLOWED_EXT = {".m4a", ".wav", ".mp3", ".mp4", ".mov"}
SPEAKERS = None

def is_temporary(path: Path) -> bool:
    name = path.name.lower()
    # Box/Windows sometimes creates partial/temp files; ignore anything suspicious
    return (
        name.endswith(".tmp") or
        name.endswith(".part") or
        name.startswith("~$") or
        name.startswith(".") or
        ".partial" in name
    )

def file_is_stable(path: Path, stable_seconds=8, checks=4) -> bool:
    """
    Wait until file size is stable across several checks.
    This handles Box still syncing/uploading.
    """
    last_size = -1
    for _ in range(checks):
        if not path.exists():
            return False
        size = path.stat().st_size
        if size != last_size:
            last_size = size
            time.sleep(stable_seconds)
        else:
            # size didn't change this interval; keep checking
            time.sleep(stable_seconds)

    # confirm one final time
    if not path.exists():
        return False
    return path.stat().st_size == last_size

def run_pipeline(audio_path: Path):
    stem = audio_path.stem

    PY = sys.executable  # <-- always use the same python running the watcher

    cmd1 = [PY, "transcribe_assemblyai.py", str(audio_path)]
    if SPEAKERS is not None:
        cmd1 += ["--speakers", str(SPEAKERS)]
    cmd2 = [PY, "identify_speakers.py",
            f"output\\{stem}_utterances.json", "enroll", f"output\\{stem}_named_script.txt"]
    cmd3 = [PY, "email_named_script.py", "--stem", stem]

    for cmd in (cmd1, cmd2, cmd3):
        print("\n> " + " ".join(map(str, cmd)))
        p = subprocess.run(cmd)
        if p.returncode != 0:
            print(f"Command failed with code {p.returncode}: {' '.join(map(str, cmd))}")
            return

    print(f"\n✅ Completed pipeline for: {audio_path.name}")


class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)

        if path.suffix.lower() not in ALLOWED_EXT:
            return
        if is_temporary(path):
            return

        print(f"📥 New file detected: {path.name}")
        print("   Waiting for Box sync to finish...")

        if file_is_stable(path):
            print("   File stable. Running pipeline.")
            run_pipeline(path)
        else:
            print("   File never became stable (moved/renamed/deleted). Skipping.")

def main():
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"👀 Watching Box folder: {WATCH_DIR.resolve()}")

    observer = Observer()
    observer.schedule(Handler(), str(WATCH_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
