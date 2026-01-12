import json
from pathlib import Path
import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: python make_script_txt.py output/meeting_utterances.json")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = input_path.with_suffix(".txt")

    utterances = json.loads(input_path.read_text(encoding="utf-8"))

    lines = []
    last_speaker = None

    for u in utterances:
        speaker = u["speaker"]
        text = u["text"].strip()

        if not text:
            continue

        if speaker != last_speaker:
            if lines:
                lines.append("")  # blank line between speakers
            lines.append(f"{speaker}:")
            lines.append(text)
        else:
            lines.append(text)

        last_speaker = speaker

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote clean script to: {output_path}")

if __name__ == "__main__":
    main()
