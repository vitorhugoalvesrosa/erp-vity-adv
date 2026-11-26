"""Formatação segura do markdown simples devolvido pela IA."""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text):
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)


@register.filter
def ai_markdown(value):
    """Converte markdown simples em HTML. Todo o texto é escapado antes."""
    if not value:
        return ""
    html = []
    list_tag = None
    paragraph = []

    def flush_paragraph():
        if paragraph:
            html.append(f"<p>{'<br>'.join(paragraph)}</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            html.append(f"</{list_tag}>")
            list_tag = None

    for raw_line in str(value).splitlines():
        line = _inline(escape(raw_line.rstrip()))
        stripped = line.strip()
        bullet = re.match(r"^[-*•]\s+(.*)", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.*)", stripped)
        heading = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if not stripped:
            flush_paragraph()
            close_list()
        elif heading:
            flush_paragraph()
            close_list()
            html.append(f"<h4>{heading.group(2)}</h4>")
        elif bullet or numbered:
            flush_paragraph()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                html.append(f"<{tag}>")
                list_tag = tag
            html.append(f"<li>{(bullet or numbered).group(1)}</li>")
        else:
            close_list()
            paragraph.append(stripped)
    flush_paragraph()
    close_list()
    return mark_safe("".join(html))
