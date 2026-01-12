"""Render summary PDF from structured data."""
import os
from pathlib import Path
from typing import Dict, List, Optional
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.units import inch


def escape(s: str) -> str:
    """Escape HTML entities for ReportLab."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_summary_pdf(output_path: Path, summary_data: Dict, source_pdf_name: Optional[str] = None) -> None:
    """
    Render a summary PDF from structured data.
    
    Args:
        output_path: Path where PDF will be saved
        summary_data: Dictionary with summary fields
        source_pdf_name: Optional name of source PDF for reference
    """
    os.makedirs(output_path.parent, exist_ok=True)
    
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Important Summary"
    )
    
    story = []
    
    def h(text: str, level: int = 2):
        """Add heading."""
        style = styles["Heading1"] if level == 1 else styles["Heading2"]
        story.append(Paragraph(escape(text), style))
        story.append(Spacer(1, 6))
    
    def p(text: str):
        """Add paragraph."""
        story.append(Paragraph(escape(text), styles["BodyText"]))
        story.append(Spacer(1, 6))
    
    def bullet(text: str):
        """Add bullet point."""
        story.append(Paragraph(f"• {escape(text)}", styles["BodyText"]))
        story.append(Spacer(1, 4))
    
    # Title
    title = summary_data.get("title", "Document Summary")
    h(title, level=1)
    
    # Date
    date = summary_data.get("date", "Not specified")
    p(f"<b>Date:</b> {escape(date)}")
    story.append(Spacer(1, 12))
    
    # Executive Summary
    h("EXECUTIVE SUMMARY")
    exec_summary = summary_data.get("executive_summary", [])
    if exec_summary and isinstance(exec_summary, list):
        for item in exec_summary:
            bullet(str(item))
    else:
        p("None")
    
    story.append(Spacer(1, 12))
    
    # Decisions
    h("KEY DECISIONS")
    decisions = summary_data.get("decisions", [])
    if decisions and isinstance(decisions, list):
        for d in decisions:
            if isinstance(d, dict):
                decision = d.get("decision", "None")
                owner = d.get("owner", "Unassigned")
                eff_date = d.get("effective_date", "Not specified")
                bullet(decision)
                if owner != "Unassigned":
                    p(f"    <i>Owner:</i> {escape(owner)}")
                if eff_date != "Not specified":
                    p(f"    <i>Effective:</i> {escape(eff_date)}")
                story.append(Spacer(1, 8))
            else:
                bullet(str(d))
    else:
        p("None")
    
    story.append(Spacer(1, 12))
    
    # Action Items
    h("ACTION ITEMS")
    actions = summary_data.get("action_items", [])
    if actions and isinstance(actions, list) and len(actions) > 0:
        # Use Paragraph objects for proper text wrapping
        table_data = [[Paragraph(escape("Owner"), styles["BodyText"]), 
                       Paragraph(escape("Task"), styles["BodyText"]), 
                       Paragraph(escape("Deadline"), styles["BodyText"]),
                       Paragraph(escape("Status"), styles["BodyText"])]]
        for a in actions:
            if isinstance(a, dict):
                owner = a.get("owner", "Unassigned")
                task = a.get("task", "None")
                deadline = a.get("deadline", "Not specified")
                status = a.get("status", "Not specified")
                # Use Paragraph objects so text wraps properly
                table_data.append([
                    Paragraph(escape(str(owner)), styles["BodyText"]),
                    Paragraph(escape(str(task)), styles["BodyText"]),
                    Paragraph(escape(str(deadline)), styles["BodyText"]),
                    Paragraph(escape(str(status)), styles["BodyText"])
                ])
        
        if len(table_data) > 1:
            t = Table(table_data, colWidths=[1.0*inch, 3.5*inch, 1.0*inch, 0.8*inch], repeatRows=1)
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("WORDWRAP", (0, 0), (-1, -1), True),
            ]))
            story.append(t)
        else:
            p("None")
    else:
        p("None")
    
    story.append(Spacer(1, 12))
    
    # Risks & Blockers
    h("RISKS & BLOCKERS")
    risks = summary_data.get("risks_blockers", [])
    if risks and isinstance(risks, list):
        for r in risks:
            if isinstance(r, dict):
                risk = r.get("risk", "None")
                severity = r.get("severity", "Med")
                owner = r.get("owner", "Unassigned")
                mitigation = r.get("mitigation", "None")
                bullet(risk)
                p(f"    <i>Severity:</i> {escape(severity)}")
                if owner != "Unassigned":
                    p(f"    <i>Owner:</i> {escape(owner)}")
                if mitigation != "None":
                    p(f"    <i>Mitigation:</i> {escape(mitigation)}")
                story.append(Spacer(1, 8))
            else:
                bullet(str(r))
    else:
        p("None")
    
    story.append(Spacer(1, 12))
    
    # Key Notes
    h("KEY NOTES")
    notes = summary_data.get("key_notes", [])
    if notes and isinstance(notes, list):
        for note in notes:
            bullet(str(note))
    else:
        p("None")
    
    story.append(Spacer(1, 12))
    
    # Metrics & Dates
    h("KEY METRICS & DATES")
    metrics = summary_data.get("metrics_dates", [])
    if metrics and isinstance(metrics, list):
        for m in metrics:
            if isinstance(m, dict):
                item = m.get("item", "None")
                value = m.get("value", "Not specified")
                notes_text = m.get("notes", "None")
                bullet(f"{item}: {value}")
                if notes_text != "None":
                    p(f"    <i>Notes:</i> {escape(notes_text)}")
                story.append(Spacer(1, 6))
            else:
                bullet(str(m))
    else:
        p("None")
    
    story.append(Spacer(1, 12))
    
    # Source Pages
    source_pages = summary_data.get("source_pages", [])
    if source_pages and isinstance(source_pages, list) and len(source_pages) > 0:
        h("SOURCE REFERENCES")
        pages_str = ", ".join([f"p. {p}" for p in source_pages[:20]])  # Limit to 20 pages
        p(f"<i>Key information found on pages: {pages_str}</i>")
    
    # Footer
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        '<font size="8" color="gray">Generated by Phi-AI</font>',
        styles["BodyText"]
    ))
    
    doc.build(story)
