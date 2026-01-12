# build_meeting_report.py
#
# Creates a clean PDF “meeting report” that includes:
#   1) Summary + insights + metrics (computed locally from transcript)
#   2) Action items (heuristics)
#   3) Named transcript (your existing output/<stem>_named_script.txt)
#
# Usage:
#   python build_meeting_report.py --stem "Square"
#   python build_meeting_report.py --named-script "output/Square_named_script.txt" --utterances "output/Square_utterances.json"
#
# Requirements:
#   pip install reportlab
#
# Outputs:
#   output/<stem>_meeting_report.pdf
#
# Notes:
# - This does NOT call any external AI. It’s deterministic and fast.
# - If you want higher-quality summaries later, we can optionally incorporate AssemblyAI “summary” fields.

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
)
from reportlab.lib.enums import TA_LEFT


# ----------------------------
# Helpers
# ----------------------------

STOPWORDS = set("""
a an and are as at be by for from has have he her hers him his i if in into is it its
just like me my no not of on or our ours she so than that the their them then there
they this to up was we were what when where which who why will with you your yours
""".split())

ACTION_PATTERNS = [
    r"\b(i[' ]?ll|i will|we[' ]?ll|we will|let[' ]?s|let us|we should|we need to|need to|should|action item|todo|follow up|circle back|send|share|schedule|set up|ping|email|text)\b",
]

DECISION_PATTERNS = [
    r"\b(we decided|decision|we're going to|we are going to|let's do|we will do|approved|agreed|finalize|locked in|greenlight)\b",
]

RISK_PATTERNS = [
    r"\b(risk|issue|problem|blocked|blocker|concern|worry|delay|late|fail|failing|bug|break)\b",
]

QUESTION_PATTERNS = [
    r"\?\s*$",
]

TIME_FMT = "%M:%S"


def sec_to_mmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m = int(seconds // 60)
    s = int(round(seconds - 60 * m))
    if s == 60:
        m += 1
        s = 0
    return f"{m:02d}:{s:02d}"


def normalize_text(t: str) -> str:
    t = t.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokenize(t: str) -> List[str]:
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s']", " ", t)
    tokens = [w for w in t.split() if w and w not in STOPWORDS and len(w) > 2]
    return tokens


def find_matches(lines: List[Tuple[str, str]]) -> Dict[str, List[Tuple[str, str]]]:
    """
    lines: list of (speaker, text)
    Returns dict with keys: actions, decisions, risks, questions
    """
    actions = []
    decisions = []
    risks = []
    questions = []

    action_re = re.compile("|".join(ACTION_PATTERNS), re.IGNORECASE)
    decision_re = re.compile("|".join(DECISION_PATTERNS), re.IGNORECASE)
    risk_re = re.compile("|".join(RISK_PATTERNS), re.IGNORECASE)
    question_re = re.compile("|".join(QUESTION_PATTERNS), re.IGNORECASE)

    for spk, txt in lines:
        if action_re.search(txt):
            actions.append((spk, txt))
        if decision_re.search(txt):
            decisions.append((spk, txt))
        if risk_re.search(txt):
            risks.append((spk, txt))
        if question_re.search(txt.strip()):
            questions.append((spk, txt))

    return {
        "actions": actions,
        "decisions": decisions,
        "risks": risks,
        "questions": questions,
    }


def parse_named_script_txt(path: Path) -> List[Tuple[str, str]]:
    """
    Expects format like:
      bobby: blah blah
      <blank line>
      theo: ...
    Returns list of (speaker, text)
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
    out = []
    for b in blocks:
        # allow "speaker: text" on first line, then maybe wrapped lines
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if not lines:
            continue
        first = lines[0]
        m = re.match(r"^([^:]{1,40}):\s*(.*)$", first)
        if m:
            spk = m.group(1).strip()
            rest_first = m.group(2).strip()
            rest = " ".join([rest_first] + lines[1:]).strip()
        else:
            spk = "Unknown"
            rest = " ".join(lines).strip()
        out.append((spk, normalize_text(rest)))
    return out


def load_utterances_json(path: Path) -> List[dict]:
    """
    Expects list of dicts with keys:
      start (seconds), end (seconds), speaker (A/B/...), text
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    cleaned = []
    for u in data:
        try:
            start = float(u.get("start", 0))
            end = float(u.get("end", 0))
            speaker = str(u.get("speaker", "Unknown"))
            text = normalize_text(str(u.get("text", "")))
            if text:
                cleaned.append({"start": start, "end": end, "speaker": speaker, "text": text})
        except Exception:
            continue
    return cleaned


@dataclass
class MeetingStats:
    duration_s: float
    speaker_word_counts: Dict[str, int]
    speaker_turn_counts: Dict[str, int]
    top_keywords: List[Tuple[str, int]]
    total_words: int


def compute_stats(named_lines: List[Tuple[str, str]], utterances: List[dict]) -> MeetingStats:
    # duration
    duration_s = 0.0
    for u in utterances:
        duration_s = max(duration_s, float(u["end"]))

    speaker_word_counts = Counter()
    speaker_turn_counts = Counter()
    all_tokens = []

    for spk, txt in named_lines:
        tokens = tokenize(txt)
        speaker_word_counts[spk] += len(tokens)
        speaker_turn_counts[spk] += 1
        all_tokens.extend(tokens)

    total_words = sum(speaker_word_counts.values())
    top_keywords = Counter(all_tokens).most_common(12)

    return MeetingStats(
        duration_s=duration_s,
        speaker_word_counts=dict(speaker_word_counts),
        speaker_turn_counts=dict(speaker_turn_counts),
        top_keywords=top_keywords,
        total_words=total_words,
    )


def build_quick_summary(named_lines: List[Tuple[str, str]], stats: MeetingStats) -> List[str]:
    """
    Simple deterministic “executive summary” bullets.
    """
    bullets = []

    # Duration + participation
    dur = sec_to_mmss(stats.duration_s) if stats.duration_s > 0 else "N/A"
    bullets.append(f"Meeting length: {dur}.")
    if stats.total_words > 0 and stats.speaker_word_counts:
        top = sorted(stats.speaker_word_counts.items(), key=lambda kv: kv[1], reverse=True)[:2]
        if top:
            bullets.append(f"Most talk time (by words): {top[0][0]} ({top[0][1]} words)" + (f", then {top[1][0]} ({top[1][1]})." if len(top) > 1 else "."))
    if stats.top_keywords:
        kw = ", ".join([w for w, _ in stats.top_keywords[:6]])
        bullets.append(f"Top topics/keywords: {kw}.")

    # A couple “signal” bullets from content heuristics
    joined = " ".join([t for _, t in named_lines])
    # count “risk-ish” and “decision-ish”
    risk_re = re.compile("|".join(RISK_PATTERNS), re.IGNORECASE)
    decision_re = re.compile("|".join(DECISION_PATTERNS), re.IGNORECASE)
    risks = len(risk_re.findall(joined))
    decisions = len(decision_re.findall(joined))
    if decisions:
        bullets.append(f"Detected decision language {decisions} time(s).")
    if risks:
        bullets.append(f"Detected risk/blocker language {risks} time(s).")

    return bullets


# ----------------------------
# PDF Builder
# ----------------------------

def build_pdf(
    out_pdf: Path,
    title: str,
    summary_bullets: List[str],
    stats: MeetingStats,
    matches: Dict[str, List[Tuple[str, str]]],
    named_lines: List[Tuple[str, str]],
):
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_pdf),
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
        title=title,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontSize=16, leading=20, spaceAfter=10))
    styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], fontSize=12.5, leading=16, spaceAfter=6))
    styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], fontSize=10.5, leading=14))
    styles.add(ParagraphStyle(name="Mono", parent=styles["BodyText"], fontName="Courier", fontSize=9.5, leading=12))
    styles.add(ParagraphStyle(name="MyBullet", parent=styles["BodyText"], fontSize=10.5, leading=14, leftIndent=14, bulletIndent=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9, leading=12))

    story = []

    # Title
    story.append(Paragraph(title, styles["H1"]))
    story.append(Spacer(1, 8))

    # Summary
    story.append(Paragraph("Executive Summary", styles["H2"]))
    for b in summary_bullets:
        story.append(Paragraph(b, styles["MyBullet"], bulletText="•"))
    story.append(Spacer(1, 10))

    # Metrics table
    story.append(Paragraph("Participation Metrics", styles["H2"]))
    dur = sec_to_mmss(stats.duration_s) if stats.duration_s > 0 else "N/A"
    story.append(Paragraph(f"Meeting length: {dur}", styles["Body"]))
    story.append(Spacer(1, 6))

    # Build speaker table - use Paragraph objects for proper text wrapping
    rows = [[Paragraph("Speaker", styles["Body"]), 
             Paragraph("Turns", styles["Body"]), 
             Paragraph("Words", styles["Body"]), 
             Paragraph("Share", styles["Body"])]]
    total = max(1, stats.total_words)
    speakers_sorted = sorted(stats.speaker_word_counts.items(), key=lambda kv: kv[1], reverse=True)

    for spk, wc in speakers_sorted:
        turns = stats.speaker_turn_counts.get(spk, 0)
        share = f"{(100.0 * wc / total):.1f}%"
        # Use Paragraph objects so text wraps properly (especially speaker names)
        rows.append([
            Paragraph(spk, styles["Body"]), 
            Paragraph(str(turns), styles["Body"]), 
            Paragraph(str(wc), styles["Body"]), 
            Paragraph(share, styles["Body"])
        ])

    tbl = Table(rows, colWidths=[2.2 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("WORDWRAP", (0, 0), (-1, -1), True),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))

    # Keywords
    story.append(Paragraph("Top Keywords", styles["H2"]))
    if stats.top_keywords:
        kw_line = ", ".join([f"{w} ({c})" for w, c in stats.top_keywords[:12]])
        story.append(Paragraph(kw_line, styles["Body"]))
    else:
        story.append(Paragraph("No keywords found.", styles["Body"]))
    story.append(Spacer(1, 10))

    # Decisions / Risks / Questions / Actions
    def section_list(title_s: str, items: List[Tuple[str, str]], max_items: int = 8):
        story.append(Paragraph(title_s, styles["H2"]))
        if not items:
            story.append(Paragraph("None detected.", styles["Body"]))
            story.append(Spacer(1, 8))
            return

        for spk, txt in items[:max_items]:
            # keep it readable
            safe = txt
            if len(safe) > 240:
                safe = safe[:240].rstrip() + "…"
            story.append(Paragraph(f"<b>{spk}:</b> {safe}", styles["Body"]))
            story.append(Spacer(1, 3))
        story.append(Spacer(1, 8))

    section_list("Decisions (heuristic)", matches.get("decisions", []))
    section_list("Risks / Blockers (heuristic)", matches.get("risks", []))
    section_list("Questions (heuristic)", matches.get("questions", []))
    section_list("Action Items (heuristic)", matches.get("actions", []), max_items=12)

    # Transcript
    story.append(PageBreak())
    story.append(Paragraph("Named Transcript", styles["H1"]))
    story.append(Spacer(1, 8))

    # Render transcript blocks
    for spk, txt in named_lines:
        story.append(KeepTogether([
            Paragraph(f"<b>{spk}</b>", styles["Body"]),
            Paragraph(txt, styles["Body"]),
            Spacer(1, 8),
        ]))

    doc.build(story)


# ----------------------------
# CLI
# ----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stem", default=None, help="Meeting stem (e.g., Square). Uses output/<stem>_named_script.txt etc.")
    parser.add_argument("--named-script", default=None, help="Path to output/<stem>_named_script.txt")
    parser.add_argument("--utterances", default=None, help="Path to output/<stem>_utterances.json (from AssemblyAI step)")
    parser.add_argument("--out", default=None, help="Output PDF path (default output/<stem>_meeting_report.pdf)")
    args = parser.parse_args()

    if args.stem:
        stem = args.stem
        named_script_path = Path(args.named_script or f"output/{stem}_named_script.txt")
        utter_path = Path(args.utterances or f"output/{stem}_utterances.json")
        out_pdf = Path(args.out or f"output/{stem}_meeting_report.pdf")
        title = f"{stem} — Meeting Report"
    else:
        if not args.named_script:
            raise SystemExit("Provide --stem <name> OR --named-script <path>.")
        named_script_path = Path(args.named_script)
        stem = named_script_path.stem.replace("_named_script", "")
        utter_path = Path(args.utterances) if args.utterances else Path(f"output/{stem}_utterances.json")
        out_pdf = Path(args.out or f"output/{stem}_meeting_report.pdf")
        title = f"{stem} — Meeting Report"

    if not named_script_path.exists():
        raise SystemExit(f"Named script not found: {named_script_path}")
    if not utter_path.exists():
        # We can still build a report without utterances, but metrics will be limited
        utterances = []
    else:
        utterances = load_utterances_json(utter_path)

    named_lines = parse_named_script_txt(named_script_path)
    if not named_lines:
        raise SystemExit(f"No transcript lines found in: {named_script_path}")

    stats = compute_stats(named_lines, utterances)
    matches = find_matches(named_lines)
    summary = build_quick_summary(named_lines, stats)

    build_pdf(
        out_pdf=out_pdf,
        title=title,
        summary_bullets=summary,
        stats=stats,
        matches=matches,
        named_lines=named_lines,
    )

    print(f"Wrote PDF report:\n  {out_pdf}")


if __name__ == "__main__":
    main()
