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
    parser.add_argument("input_file", help=r"e.g. input\Square.m4a")
    parser.add_argument("--speakers", type=int, default=4)
    args = parser.parse_args()

    in_path = Path(args.input_file)
    stem = in_path.stem

    # 1) Transcribe
    cmd1 = ["python", "transcribe_assemblyai.py", str(in_path), "--speakers", str(args.speakers)]
    run(cmd1)

    # 2) Identify speakers -> named script
    utter_json = Path("output") / f"{stem}_utterances.json"
    out_txt = Path("output") / f"{stem}_named_script.txt"
    cmd2 = ["python", "identify_speakers.py", str(utter_json), "enroll", str(out_txt)]
    run(cmd2)

    # 3) Email it
    # Your email script currently uses MEETING_STEM="Square" inside it.
    # Two options:
    #   A) Update email_named_script.py to accept --stem argument (best)
    #   B) For now, copy the output to Square_named_script.txt (hacky)
    #
    # Best: update email_named_script.py to accept --stem. For now we’ll do the best path:
    cmd3 = ["python", "email_named_script.py", "--stem", stem]
    run(cmd3)

    print("\nDONE.")

if __name__ == "__main__":
    main()
