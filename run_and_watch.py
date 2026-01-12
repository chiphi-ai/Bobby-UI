import time
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

WATCH_DIR = Path("input") / "dropbox"   # choose your watched folder
ALLOWED_EXT = {".m4a", ".wav", ".mp3", ".mp4", ".mov"}

SPEAKERS = 4

def file_is_stable(path: Path, seconds=3, checks=3) -> bool:
    """Return True once size stops changing for a few checks."""
    last = None
    for _ in range(checks):
        if not path.exists():
            return False
        size = path.stat().st_size
        if last is not None and size != last:
            last = size
        else:
            last = size
        time.sleep(seconds)
    # if size hasn't changed across the window, assume done writing
    return True

def run_pipeline(path: Path):
    cmd = ["python", "run_pipeline.py", str(path), "--speakers", str(SPEAKERS)]
    print("\n> " + " ".join(cmd))
    subprocess.run(cmd)

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() not in ALLOWED_EXT:
            return

        print(f"Detected new file: {p.name}")
        # wait for recording to finish copying
        if file_is_stable(p):
            print(f"File stable. Running pipeline for: {p.name}")
            run_pipeline(p)
        else:
            print(f"File not stable / vanished: {p.name}")

def main():
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Watching folder: {WATCH_DIR.resolve()}")

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
