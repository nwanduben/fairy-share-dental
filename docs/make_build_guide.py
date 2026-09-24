"""Render docs/build_guide_content.py into a styled PDF.

    .venv/bin/python docs/make_build_guide.py
"""
from __future__ import annotations

import sys
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_guide_content import BYLINE, CONTENT, SUBTITLE, TITLE  # noqa: E402

OUT = Path(__file__).resolve().parent / "Fairy-Share-Dental-Build-Guide.pdf"

INK = colors.HexColor("#14181a")
MUTED = colors.HexColor("#5b6668")
BLUE = colors.HexColor("#1f5fbf")
RULE = colors.HexColor("#d9dfe1")
CODE_BG = colors.HexColor("#f2f5f6")
NOTE_BG = colors.HexColor("#eef4ff")
WARN_BG = colors.HexColor("#fff4e6")

base = getSampleStyleSheet()
S = {
    "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=19, leading=23,
                         textColor=INK, spaceBefore=6, spaceAfter=10),
    "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13.5, leading=17,
                         textColor=INK, spaceBefore=16, spaceAfter=5),
    "h3": ParagraphStyle("h3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=11, leading=14,
                         textColor=BLUE, spaceBefore=12, spaceAfter=3),
    "p": ParagraphStyle("p", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=15.2,
                        textColor=INK, alignment=TA_LEFT, spaceAfter=7),
    "li": ParagraphStyle("li", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=14.6,
                         textColor=INK, spaceAfter=4),
    "code": ParagraphStyle("code", parent=base["BodyText"], fontName="Courier", fontSize=8.4, leading=11.6,
                           textColor=INK),
    "prompt": ParagraphStyle("prompt", parent=base["BodyText"], fontName="Courier", fontSize=8.6, leading=12.4,
                             textColor=INK),
    "callout": ParagraphStyle("callout", parent=base["BodyText"], fontName="Helvetica", fontSize=9.4, leading=13.6,
                              textColor=INK),
    "cell": ParagraphStyle("cell", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=12.4,
                           textColor=INK, spaceAfter=0),
    "cellhead": ParagraphStyle("cellhead", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9,
                               leading=12.4, textColor=colors.white, spaceAfter=0),
    "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=27, leading=31,
                            textColor=INK, spaceAfter=8),
    "subtitle": ParagraphStyle("subtitle", parent=base["BodyText"], fontName="Helvetica", fontSize=13, leading=18,
                               textColor=MUTED, spaceAfter=4),
    "byline": ParagraphStyle("byline", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13,
                             textColor=MUTED),
}

WIDTH = LETTER[0] - 2 * inch


def rich(text: str) -> str:
    """Escape, then re-enable **bold** and `code`."""
    out = escape(text).replace("\n", "<br/>")
    while "**" in out:
        out = out.replace("**", "<b>", 1).replace("**", "</b>", 1)
    while out.count("`") >= 2:
        out = out.replace("`", '<font face="Courier" size="9">', 1).replace("`", "</font>", 1)
    return out


def boxed(text: str, bg, border, style_key: str, mono: bool = False):
    body = escape(text).replace("\n", "<br/>") if mono else rich(text)
    cell = Paragraph(body, S[style_key])
    t = Table([[cell]], colWidths=[WIDTH])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.6, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return t


def build_table(rows: list[list[str]]):
    header, *body = rows
    data = [[Paragraph(rich(c), S["cellhead"]) for c in header]]
    data += [[Paragraph(rich(c), S["cell"]) for c in r] for r in body]
    ncols = len(header)
    if ncols == 2:
        widths = [WIDTH * 0.30, WIDTH * 0.70]
    elif ncols == 3:
        widths = [WIDTH * 0.22, WIDTH * 0.50, WIDTH * 0.28]
    else:
        widths = [WIDTH / ncols] * ncols
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fa")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def flow():
    story = [
        Spacer(1, 1.6 * inch),
        Paragraph(TITLE, S["title"]),
        Paragraph(SUBTITLE, S["subtitle"]),
        Spacer(1, 10),
        Paragraph(BYLINE, S["byline"]),
        Spacer(1, 22),
        boxed("Everything in this guide was built and run against Open Dental's public developer test database with "
              "invented patients. It is a demonstration system and is not HIPAA compliant.", NOTE_BG, RULE, "callout"),
        PageBreak(),
    ]
    for kind, payload in CONTENT:
        if kind == "pagebreak":
            story.append(PageBreak())
        elif kind in ("h1", "h2", "h3"):
            story.append(Paragraph(rich(payload), S[kind]))
        elif kind == "p":
            story.append(Paragraph(rich(payload), S["p"]))
        elif kind == "bullets":
            story.append(ListFlowable(
                [ListItem(Paragraph(rich(b), S["li"]), leftIndent=14, value="circle") for b in payload],
                bulletType="bullet", start="•", leftIndent=14, bulletFontSize=7, spaceAfter=8,
            ))
        elif kind == "code":
            story.append(KeepTogether(boxed(payload, CODE_BG, RULE, "code", mono=True)))
            story.append(Spacer(1, 9))
        elif kind == "prompt":
            story.append(Paragraph("Prompt to paste:", S["h3"]))
            story.append(boxed(payload, CODE_BG, BLUE, "prompt", mono=True))
            story.append(Spacer(1, 9))
        elif kind == "note":
            story.append(KeepTogether(boxed(payload, NOTE_BG, RULE, "callout")))
            story.append(Spacer(1, 9))
        elif kind == "warn":
            story.append(KeepTogether(boxed(payload, WARN_BG, colors.HexColor("#e0a955"), "callout")))
            story.append(Spacer(1, 9))
        elif kind == "table":
            story.append(build_table(payload))
            story.append(Spacer(1, 11))
    return story


def decorate(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(inch, 0.62 * inch, "Fairy Share Dental — AI receptionist build guide")
        canvas.drawRightString(LETTER[0] - inch, 0.62 * inch, str(doc.page))
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(inch, 0.8 * inch, LETTER[0] - inch, 0.8 * inch)
    canvas.restoreState()


def main() -> None:
    doc = BaseDocTemplate(str(OUT), pagesize=LETTER, title=TITLE, author="Ben Nwandu",
                          leftMargin=inch, rightMargin=inch, topMargin=0.9 * inch, bottomMargin=inch)
    frame = Frame(inch, inch, WIDTH, LETTER[1] - 1.9 * inch, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
    doc.build(flow())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
