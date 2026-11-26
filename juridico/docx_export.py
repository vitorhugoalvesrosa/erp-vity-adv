"""Exporta o texto de uma peça (markdown simples) para .docx."""

import re
from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*\s][^*]*?\*)")


def _add_runs(paragraph, text):
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        else:
            paragraph.add_run(part)


def peca_para_docx(peca):
    document = Document()
    for section in document.sections:
        section.top_margin = Cm(3)
        section.left_margin = Cm(3)
        section.bottom_margin = Cm(2)
        section.right_margin = Cm(2)

    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(6)

    for raw in (peca.conteudo or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)", stripped)
        bullet = re.match(r"^[-*•]\s+(.*)", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.*)", stripped)
        if heading:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(12)
            if len(heading.group(1)) == 1:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(heading.group(2).replace("**", ""))
            run.bold = True
        elif bullet:
            paragraph = document.add_paragraph(style="List Bullet")
            _add_runs(paragraph, bullet.group(1))
        elif numbered:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(1)
            paragraph.paragraph_format.first_line_indent = Cm(-0.6)
            _add_runs(paragraph, f"{numbered.group(1)}. {numbered.group(2)}")
        else:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            paragraph.paragraph_format.first_line_indent = Cm(2)
            _add_runs(paragraph, stripped)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
