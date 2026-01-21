# run_named_script.py
# Usage:
#   python run_named_script.py input\normal.m4a enroll --speakers 4

import argparse
import subprocess
import sys
from pathlib import Path

def run(cmd):
    print("\n> " + " ".join(cmd))
    p = subprocess.run(cmd)
    if p.returncode != 0:
        sys.exit(p.returncode)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file")
    parser.add_argument("enroll_dir")
    parser.add_argument("--speakers", type=int, default=None)
    args = parser.parse_args()

    in_path = Path(args.input_file)
    stem = in_path.stem

    # 1) transcribe (old script)
    cmd1 = ["python", "transcribe.py", str(in_path)]
    if args.speakers is not None:
        cmd1 += ["--speakers", str(args.speakers)]
    run(cmd1)

    # 2) identify (old script)
    utter_json = Path("output") / f"{stem}_utterances.json"
    out_txt = Path("output") / f"{stem}_named_script.txt"
    cmd2 = ["python", "identify_speakers.py", str(utter_json), args.enroll_dir, str(out_txt)]
    run(cmd2)

    print(f"\nDONE: {out_txt}")

if __name__ == "__main__":
    main()