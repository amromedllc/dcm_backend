"""Sanitize rich-text HTML before it's stored or rendered.

Used by the Knowledge Base authoring paths that accept HTML: the superadmin
rich-text editor (manual CRUD) and the Word-import apply step
(apps.central_library.imports). Both feed the same safe subset the provider
page knows how to render (shared/docx_blocks.py produces the same tags).
"""
from __future__ import annotations

import nh3

KB_HTML_TAGS = {
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'strong', 'em', 'u', 'a', 'br',
    'table', 'tr', 'td', 'img', 'video', 'source',
}
KB_HTML_ATTRIBUTES = {
    'a': {'href'},
    'img': {'src', 'alt'},
    'video': {'controls'},
    'source': {'src', 'type'},
}


def sanitize_kb_html(value: str | None) -> str:
    if not value:
        return ''
    return nh3.clean(
        value,
        tags=KB_HTML_TAGS,
        attributes=KB_HTML_ATTRIBUTES,
        link_rel='noopener noreferrer',
    )
