"""Markdown → PDF with fpdf2 (FR-029, BR-028).

PDF core fonts cannot show Greek, so a Unicode TrueType font is required. It is taken from
``documents.pdf_font_dir`` if configured, otherwise from the usual system locations
(Arial on Windows and macOS, DejaVu on Linux).
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from panos_core.documents.markdown import (
    Code,
    Heading,
    ListBlock,
    Paragraph,
    Quote,
    Rule,
    Table,
    parse,
    plain,
    runs,
)


class FontNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class FontSet:
    regular: Path
    bold: Path
    italic: Path
    mono: Path


# (regular, bold, italic, mono) file names, tried in order per directory.
_CANDIDATES: Final = [
    ("arial.ttf", "arialbd.ttf", "ariali.ttf", "cour.ttf"),
    ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Courier New.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSansMono.ttf"),
    (
        "LiberationSans-Regular.ttf",
        "LiberationSans-Bold.ttf",
        "LiberationSans-Italic.ttf",
        "LiberationMono-Regular.ttf",
    ),
]


def _system_dirs(platform: str = sys.platform) -> list[Path]:
    if platform == "win32":
        import os

        return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    if platform == "darwin":
        return [Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts")]
    roots = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
    return [d for root in roots if root.is_dir() for d in [root, *root.rglob("*")] if d.is_dir()]


def find_fonts(font_dir: Path | None = None) -> FontSet:
    dirs = [font_dir] if font_dir else _system_dirs()
    for names in _CANDIDATES:
        for directory in dirs:
            paths = [directory / n for n in names]
            if all(p.is_file() for p in paths[:3]):
                mono = paths[3] if paths[3].is_file() else paths[0]
                return FontSet(paths[0], paths[1], paths[2], mono)
    raise FontNotFoundError(
        "no Unicode TrueType font found for PDF export (looked for Arial, DejaVu Sans, "
        "Liberation Sans); set documents.pdf_font_dir to a folder with one"
    )


class _Pdf(FPDF):
    def __init__(self, title: str) -> None:
        super().__init__(format="A4")
        self.doc_title = title

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("body", size=8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"{self.doc_title} · {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)


_SIZES: Final = {1: 18, 2: 14, 3: 12, 4: 11, 5: 10.5, 6: 10.5}
BODY: Final = 10
LINE: Final = 5.2


def _write_runs(pdf: FPDF, text: str, size: float = BODY) -> None:
    for r in runs(text):
        if r.code:
            pdf.set_font("mono", size=size - 0.5)
        else:
            pdf.set_font(
                "body", style=("B" if r.bold else "") + ("I" if r.italic else ""), size=size
            )
        pdf.write(LINE, r.text)
    pdf.set_font("body", size=size)


def render_pdf(markdown: str, *, title: str, fonts: FontSet | None = None) -> bytes:
    fonts = fonts or find_fonts()
    pdf = _Pdf(title)
    pdf.set_title(title)
    pdf.set_author("Panos")
    pdf.set_creator("Panos")
    pdf.add_font("body", "", str(fonts.regular))
    pdf.add_font("body", "B", str(fonts.bold))
    pdf.add_font("body", "I", str(fonts.italic))
    pdf.add_font("body", "BI", str(fonts.bold))
    pdf.add_font("mono", "", str(fonts.mono))
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    pdf.set_font("body", size=BODY)
    left = pdf.l_margin

    first = True
    for block in parse(markdown):
        if isinstance(block, Heading):
            if block.level == 1 and not first:
                pdf.add_page()  # each document in a combined export starts on a new page
            pdf.ln(3 if block.level > 1 else 0)
            pdf.set_font("body", style="B", size=_SIZES[block.level])
            pdf.multi_cell(
                0, _SIZES[block.level] * 0.5, plain(block.text), new_x=XPos.LMARGIN, new_y=YPos.NEXT
            )
            pdf.ln(1.5)
            pdf.set_font("body", size=BODY)
        elif isinstance(block, Paragraph):
            _write_runs(pdf, block.text)
            pdf.ln(LINE + 1.5)
        elif isinstance(block, Quote):
            pdf.set_text_color(90, 90, 90)
            pdf.set_left_margin(left + 4)
            pdf.set_x(left + 4)
            _write_runs(pdf, block.text, size=BODY - 0.5)
            pdf.set_left_margin(left)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(LINE + 2)
        elif isinstance(block, ListBlock):
            for item in block.items:
                indent = left + 4 + item.depth * 6
                pdf.set_x(indent - 4)
                pdf.set_font("body", size=BODY)
                marker = f"{item.number}." if item.ordered else "•"
                pdf.cell(4, LINE, marker)
                pdf.set_left_margin(indent)
                _write_runs(pdf, item.text)
                pdf.set_left_margin(left)
                pdf.ln(LINE + 0.8)
            pdf.ln(1.5)
        elif isinstance(block, Table):
            pdf.set_font("body", size=BODY - 1)
            # Column widths follow content length (bounded), so text columns get the room.
            widths = [
                min(40, max(4, len(plain(h)), *(len(plain(r[i])) for r in block.rows)))
                for i, h in enumerate(block.header)
            ]
            with pdf.table(
                line_height=LINE,
                text_align="LEFT",
                first_row_as_headings=True,
                col_widths=tuple(widths),
            ) as table:
                header = table.row()
                for text in block.header:
                    header.cell(plain(text))
                for row in block.rows:
                    cells = table.row()
                    for text in row:
                        cells.cell(plain(text))
            pdf.ln(3)
            pdf.set_font("body", size=BODY)
        elif isinstance(block, Code):
            if block.language == "mermaid":
                pdf.set_font("body", style="I", size=BODY - 1)
                pdf.cell(0, LINE, "Diagram (Mermaid source):", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("mono", size=BODY - 1.5)
            pdf.set_fill_color(245, 245, 245)
            pdf.multi_cell(
                0, LINE - 0.8, block.text, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT
            )
            pdf.ln(2)
            pdf.set_font("body", size=BODY)
        elif isinstance(block, Rule):
            y = pdf.get_y() + 2
            pdf.line(left, y, pdf.w - pdf.r_margin, y)
            pdf.ln(5)
        first = False
    return bytes(pdf.output())
