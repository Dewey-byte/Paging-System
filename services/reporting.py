"""CSV and PDF report generation (ReportLab)."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

CSV_COLUMNS = ["Date/Time", "Title", "Type", "Category", "Initiated By", "Source", "Target Zones", "Duration (s)",
               "Status", "Details", "Ended By", "Outputs"]


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}:{sec:02d}"


def export_history_csv(records, path: Path, type_labels: dict, status_labels: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for r in records:
            writer.writerow([r.started_at, r.title, type_labels.get(r.broadcast_type, r.broadcast_type), r.category,
                             r.initiated_by_name, r.source, r.zone_names, round(r.duration_seconds or 0, 1),
                             status_labels.get(r.status, r.status), r.status_detail, r.ended_by_name,
                             r.devices_summary])
    return path


def export_activity_csv(entries, path: Path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date/Time", "User", "Level", "Module", "Action", "Details"])
        for e in entries:
            writer.writerow([e.created_at, e.username, e.level, e.module, e.action, e.details])
    return path


def export_history_pdf(records, path: Path, school: str, filters_text: str, summary: dict, type_labels: dict,
                       status_labels: dict) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    navy = colors.HexColor("#172554")
    blue = colors.HexColor("#2563EB")
    muted = colors.HexColor("#64748B")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("t", parent=styles["Title"], fontSize=18, textColor=navy, alignment=0,
                                 spaceAfter=2)
    sub_style = ParagraphStyle("s", parent=styles["Normal"], fontSize=9, textColor=muted)
    cell = ParagraphStyle("c", parent=styles["Normal"], fontSize=7.5, leading=9)
    head = ParagraphStyle("h", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, doc.pagesize[1] - 6 * mm, doc.pagesize[0], 6 * mm, stroke=0, fill=1)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(12 * mm, 8 * mm, f"{school} - School Paging System")
        canvas.drawRightString(doc.pagesize[0] - 12 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title="Broadcast History Report",
                            author=school)
    story = [
        Paragraph("Broadcast History Report", title_style),
        Paragraph(f"{school} &nbsp;&bull;&nbsp; Generated {datetime.now().strftime('%B %d, %Y %I:%M %p')}",
                  sub_style),
        Paragraph(f"Filters: {filters_text or 'All records'}", sub_style),
        Spacer(1, 5 * mm),
    ]
    stats = [["Total broadcasts", "Completed", "Stopped / interrupted", "Failed / missed", "Emergency alarms",
              "Total airtime"],
             [str(summary.get("total", 0)), str(summary.get("completed", 0)), str(summary.get("stopped", 0)),
              str(summary.get("failed", 0)), str(summary.get("alarms", 0)), _fmt_duration(summary.get("airtime", 0))]]
    stats_table = Table(stats, colWidths=[44 * mm] * 6)
    stats_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica"), ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), muted), ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 14), ("TEXTCOLOR", (0, 1), (-1, 1), navy),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [stats_table, Spacer(1, 5 * mm)]
    header = ["Date / Time", "Title", "Type", "Category", "Initiated by", "Zones", "Duration", "Status"]
    rows = [[Paragraph(h, head) for h in header]]
    status_colors = {"completed": "#15803D", "cancelled": "#B45309", "interrupted": "#B45309", "failed": "#B91C1C",
                     "missed": "#B91C1C", "in_progress": "#1D4ED8"}
    for r in records:
        status = status_labels.get(r.status, r.status)
        rows.append([
            Paragraph(r.started_at, cell), Paragraph(_escape(r.title), cell),
            Paragraph(type_labels.get(r.broadcast_type, r.broadcast_type), cell), Paragraph(_escape(r.category), cell),
            Paragraph(_escape(r.initiated_by_name), cell), Paragraph(_escape(r.zone_names), cell),
            Paragraph(_fmt_duration(r.duration_seconds), cell),
            Paragraph(f'<font color="{status_colors.get(r.status, "#334155")}"><b>{status}</b></font>', cell),
        ])
    if len(rows) == 1:
        rows.append([Paragraph("No broadcasts match the selected filters.", cell)] + [""] * 7)
    table = Table(rows, colWidths=[30 * mm, 50 * mm, 26 * mm, 34 * mm, 30 * mm, 56 * mm, 16 * mm, 21 * mm],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), blue), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return path


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
