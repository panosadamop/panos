"""Document rendering (FR-029, BR-028): Markdown to Word and PDF, plus helpers."""

from panos_core.documents.docx_render import render_docx
from panos_core.documents.pdf_render import FontNotFoundError, FontSet, find_fonts, render_pdf

__all__ = ["FontNotFoundError", "FontSet", "find_fonts", "render_docx", "render_pdf"]
