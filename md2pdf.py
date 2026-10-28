#!/usr/bin/env python3
"""
Render a Markdown file to PDF with reportlab.

Covers the Markdown subset used in this repository's documentation: ATX headings,
paragraphs, block quotes, fenced code blocks, bullet and numbered lists (nested, with code
blocks inside items), pipe tables, and inline bold, code spans and links. Links to
"#heading" anchors work inside the PDF; relative links are made absolute with --base-url
(without it they are rendered as plain text). Headings become PDF bookmarks.

Requires reportlab (pip install reportlab).

Usage: md2pdf.py [--base-url URL] SRC.md [OUT.pdf]
"""
import argparse
import os
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

MARGIN = 2 * cm
WIDTH = A4[0] - 2 * MARGIN
BASE = 10           # body font size (pt)
CODE = 8            # code block font size
TABLE = 8.5         # table text font size
INDENT = 14         # per list level (pt)

# TrueType fonts with arrows and dashes; the first complete set found is used.
FONT_SETS = [
    ("/System/Library/Fonts/Supplemental/", "Arial.ttf", "Arial Bold.ttf",
     "Courier New.ttf", "Courier New Bold.ttf"),                                    # macOS
    ("/usr/share/fonts/truetype/dejavu/", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
     "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),                              # Linux
    ("C:/Windows/Fonts/", "arial.ttf", "arialbd.ttf", "cour.ttf", "courbd.ttf"),   # Windows
]
# Used with reportlab's built-in fonts, which only cover Latin-1.
ASCII_SUBST = {"\u2192": "->", "\u2190": "<-", "\u2013": "-", "\u2014": "-", "\u2265": ">=",
               "\u2264": "<="}


def setup_fonts():
    """Register a Unicode font set if available; return (sans, sans_bold, mono, ascii_only)."""
    for d, *files in FONT_SETS:
        paths = [os.path.join(d, f) for f in files]
        if all(os.path.exists(p) for p in paths):
            names = ["Doc", "Doc-Bold", "DocMono", "DocMono-Bold"]
            for n, p in zip(names, paths):
                pdfmetrics.registerFont(TTFont(n, p))
            pdfmetrics.registerFontFamily("Doc", normal="Doc", bold="Doc-Bold",
                                          italic="Doc", boldItalic="Doc-Bold")
            pdfmetrics.registerFontFamily("DocMono", normal="DocMono", bold="DocMono-Bold",
                                          italic="DocMono", boldItalic="DocMono-Bold")
            return "Doc", "Doc-Bold", "DocMono", False
    return "Helvetica", "Helvetica-Bold", "Courier", True


SANS, BOLD, MONO, ASCII_ONLY = setup_fonts()


def style(name, **kw):
    base = dict(fontName=SANS, fontSize=BASE, leading=BASE * 1.4, alignment=TA_LEFT,
                spaceAfter=6)
    base.update(kw)
    return ParagraphStyle(name, **base)


ST = {
    "body": style("body"),
    "h1": style("h1", fontName=BOLD, fontSize=18, leading=22, spaceBefore=4, spaceAfter=10,
                keepWithNext=1),
    "h2": style("h2", fontName=BOLD, fontSize=14, leading=18, spaceBefore=12, spaceAfter=6,
                keepWithNext=1),
    "h3": style("h3", fontName=BOLD, fontSize=12, leading=15, spaceBefore=8, spaceAfter=4,
                keepWithNext=1),
    "quote": style("quote", leftIndent=10, borderPadding=(2, 0, 2, 6), borderColor=colors.grey,
                   textColor=colors.HexColor("#444444"), backColor=colors.HexColor("#f6f6f6")),
    "code": style("code", fontName=MONO, fontSize=CODE, leading=CODE * 1.25,
                  backColor=colors.HexColor("#f4f4f4"), borderPadding=4, spaceBefore=4,
                  spaceAfter=10),
    "cell": style("cell", fontSize=TABLE, leading=TABLE * 1.3, spaceAfter=0),
    "head": style("head", fontName=BOLD, fontSize=TABLE, leading=TABLE * 1.3, spaceAfter=0),
}


def slug(text):
    """GitHub-style heading anchor."""
    text = re.sub(r"[`*]", "", text.lower())
    return re.sub(r"[^\w\- ]", "", text).strip().replace(" ", "-")


def plain(text):
    """Text as displayed, without Markdown markup (for widths and bookmarks)."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[`*]", "", text)


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(text, base_url, code_size=None):
    """Convert inline Markdown to reportlab paragraph markup."""
    code_size = code_size or BASE * 0.9
    spans = []

    def code(m):
        spans.append(f'<font name="{MONO}" size="{code_size}">{esc(m.group(1))}</font>')
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", code, text)   # protect code spans from further markup
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    def link(m):
        label, url = m.group(1), m.group(2)
        if url.startswith("#"):
            return f'<a href="{url}" color="#0645ad">{label}</a>'
        if not re.match(r"[a-z]+:", url):
            if not base_url:
                return label
            url = base_url.rstrip("/") + "/" + url
        return f'<a href="{url}" color="#0645ad"><u>{label}</u></a>'

    text = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", link, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


def text_width(text, size, mono=False, bold=False):
    return pdfmetrics.stringWidth(text, MONO if mono else BOLD if bold else SANS, size)


def col_widths(rows):
    """Fit columns into the page: each gets its longest word, the rest is shared by need."""
    n = len(rows[0])
    lo, hi = [0.0] * n, [0.0] * n
    for r, row in enumerate(rows):
        for i, cell in enumerate(row):
            mono = cell.startswith("`")
            size = TABLE * 0.8 if mono else TABLE
            words = plain(cell).split() or [""]
            lo[i] = max(lo[i], max(text_width(w, size, mono, r == 0) for w in words) + 13)
            hi[i] = max(hi[i], text_width(plain(cell), size, mono, r == 0) + 13)
    if sum(hi) <= WIDTH:
        return hi
    spare = max(WIDTH - sum(lo), 0)
    need = sum(h - l for h, l in zip(hi, lo)) or 1
    return [l + spare * (h - l) / need for h, l in zip(hi, lo)]


def table(lines, base_url):
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
    rows = [r for r in rows if not all(re.fullmatch(r":?-+:?", c) for c in r)]
    # shrink code in tables a bit more so long hashes fit on one line
    cells = [[Paragraph(inline(c, base_url, TABLE * 0.8), ST["head" if i == 0 else "cell"])
              for c in r] for i, r in enumerate(rows)]
    t = Table(cells, colWidths=col_widths(rows), repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [t, Spacer(1, 8)]


def code_block(lines, indent):
    width = WIDTH - indent - 8
    max_chars = int(width / text_width("0", CODE, mono=True))
    s = ParagraphStyle("c%d" % indent, parent=ST["code"], leftIndent=indent + 4,
                       rightIndent=4)
    return Preformatted("\n".join(lines), s, maxLineLength=max_chars, newLineChars="")


def parse(md, base_url):
    """Turn Markdown text into a list of flowables."""
    if ASCII_ONLY:
        for k, v in ASCII_SUBST.items():
            md = md.replace(k, v)
    lines = md.splitlines()
    out = []
    i = 0
    item = re.compile(r"^(\s*)([-*]|\d+\.) +(.*)$")
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        ind = len(line) - len(line.lstrip())
        if not stripped:
            i += 1
        elif stripped.startswith("```"):
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i][ind:] if lines[i][:ind].isspace() else lines[i].lstrip())
                i += 1
            i += 1
            # code inside a list item is indented like the item's text
            out.append(code_block(body, (ind // 2) * INDENT if ind else 0))
        elif re.match(r"#{1,6} ", stripped):
            level = len(stripped.split()[0])
            text = stripped[level + 1:]
            p = Paragraph(f'<a name="{slug(text)}"/>' + inline(text, base_url),
                          ST["h%d" % min(level, 3)])
            p.outline = (plain(text), level - 1, slug(text))
            out.append(p)
            i += 1
        elif stripped.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.extend(table(block, base_url))
        elif stripped.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(Paragraph(inline(" ".join(block), base_url), ST["quote"]))
        elif item.match(line):
            m = item.match(line)
            text = [m.group(3)]
            i += 1
            # continuation lines are indented and not a new item or code fence
            while (i < len(lines) and lines[i].strip() and lines[i][:1].isspace()
                   and not item.match(lines[i]) and not lines[i].strip().startswith("```")):
                text.append(lines[i].strip())
                i += 1
            level = ind // 2 + 1
            bullet = m.group(2) if m.group(2)[0].isdigit() else "\u2022"
            s = ParagraphStyle("li%d" % level, parent=ST["body"], spaceAfter=1,
                               leftIndent=level * INDENT + 4, bulletIndent=(level - 1) * INDENT + 4)
            out.append(Paragraph(inline(" ".join(text), base_url), s, bulletText=bullet))
            if i >= len(lines) or not lines[i].strip():
                out.append(Spacer(1, 3))
        else:
            block = []
            while (i < len(lines) and lines[i].strip() and not item.match(lines[i])
                   and not re.match(r"(#{1,6} |```|\||>)", lines[i].strip())):
                block.append(lines[i].strip())
                i += 1
            out.append(Paragraph(inline(" ".join(block), base_url), ST["body"]))
    return out


class Doc(SimpleDocTemplate):
    def afterFlowable(self, f):
        outline = getattr(f, "outline", None)
        if outline:
            title, level, key = outline
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level=level, closed=level > 0)


def footer(canv, doc):
    canv.saveState()
    canv.setFont(SANS, 8)
    canv.setFillColor(colors.grey)
    canv.drawCentredString(A4[0] / 2, MARGIN / 2, str(doc.page))
    canv.restoreState()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("src", help="Markdown file")
    ap.add_argument("out", nargs="?", help="PDF file (default: SRC with .pdf)")
    ap.add_argument("--base-url", help="prefix for relative links, e.g. a GitHub blob URL")
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.src)[0] + ".pdf"
    with open(a.src, encoding="utf-8") as f:
        md = f.read()
    title = next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")),
                 os.path.basename(a.src))
    doc = Doc(out, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
              bottomMargin=MARGIN, title=plain(title), author="")
    doc.build(parse(md, a.base_url), onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {out}" + (" (built-in fonts, non-Latin-1 characters replaced)"
                            if ASCII_ONLY else ""))


if __name__ == "__main__":
    sys.exit(main())
