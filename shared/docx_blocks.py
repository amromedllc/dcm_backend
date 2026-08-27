"""Parse a .docx into an ordered list of addressable "blocks".

Used by the Knowledge Base Word-import flow: an author writes an article in
Word, uploads it, and the admin maps each block onto a target field
(module title / overview / a topic's summary / a topic's items ...). The
frontend preview renders these same blocks, so what the admin sees is exactly
what they can assign.

Block shape (one dict per mappable unit, in document order):

    {
      "id":     "b3",                 # stable within one parse, order-based
      "kind":   "heading" | "paragraph" | "list_item" | "table",
      "level":  1..6 | None,          # heading level (None for everything else)
      "indent": 0..8,                 # list nesting depth, for preview only
      "list":   "bullet" | "number" | None,
      "text":   "plain text",         # inline formatting stripped
      "html":   "<strong>x</strong>", # sanitised inline subset: strong/em/u/a
      "rows":   [["a","b"], ...],     # tables only
    }

Design notes:
  - Lists are captured but NOT un-flattened: every list paragraph is a
    "list_item" regardless of nesting; `indent` is kept only so the preview
    can show structure.
  - Images are dropped; the count is returned separately so the UI can warn.
  - `html` is generated here from Word runs, so it is already a known-safe
    subset -- the frontend still sanitises before rendering.
"""
from __future__ import annotations

import html as _html
import re

from docx import Document
from docx.oxml.ns import qn

_HEADING_RE = re.compile(r'^\s*heading\s+([1-9])\s*$', re.IGNORECASE)
_MAX_INDENT = 8


def parse_docx_blocks(file) -> dict:
    """file: a path or file-like object positioned at the start of a .docx.

    Returns {"blocks": [...], "image_count": int}.
    Raises ValueError if the file is not a readable .docx.
    """
    try:
        document = Document(file)
    except Exception as exc:  # python-docx raises PackageNotFoundError, KeyError, ...
        raise ValueError('The file could not be read as a Word (.docx) document.') from exc

    numbering = _NumberingResolver(document)
    blocks: list[dict] = []
    image_count = 0
    counter = 0

    body = document.element.body
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            paragraph = Paragraph(child, document)
            para_images = _count_images(paragraph)
            image_count += para_images

            text = paragraph.text.strip()
            if not text:
                continue

            counter += 1
            block = {'id': f'b{counter}', 'level': None, 'indent': 0, 'list': None}

            heading_level = _heading_level(paragraph)
            list_info = numbering.for_paragraph(paragraph)

            if heading_level:
                block['kind'] = 'heading'
                block['level'] = heading_level
            elif list_info:
                block['kind'] = 'list_item'
                block['list'] = list_info['format']
                block['indent'] = list_info['indent']
            else:
                block['kind'] = 'paragraph'

            block['text'] = text
            block['html'] = _inline_html(paragraph)
            blocks.append(block)

        elif child.tag == qn('w:tbl'):
            table = Table(child, document)
            rows = [
                [_cell_text(cell) for cell in row.cells]
                for row in table.rows
            ]
            rows = [r for r in rows if any(c.strip() for c in r)]
            if not rows:
                continue
            counter += 1
            blocks.append({
                'id': f'b{counter}',
                'kind': 'table',
                'level': None,
                'indent': 0,
                'list': None,
                'rows': rows,
                'text': '\n'.join(' | '.join(r) for r in rows),
                'html': _table_html(rows),
            })

    return {'blocks': blocks, 'image_count': image_count}


# --------------------------------------------------------------------------- #
# Inline formatting -> safe HTML subset
# --------------------------------------------------------------------------- #

def _inline_html(paragraph) -> str:
    """Render a paragraph's runs as HTML using only <strong>/<em>/<u>/<a>."""
    out: list[str] = []

    # Hyperlinks (python-docx >= 1.1) expose their own runs; iterate the
    # paragraph's content in order so link text lands in the right place.
    try:
        content = list(paragraph.iter_inner_content())
    except AttributeError:  # very old python-docx
        content = list(paragraph.runs)

    for item in content:
        if hasattr(item, 'runs') and hasattr(item, 'address'):  # Hyperlink
            inner = ''.join(_run_html(r) for r in item.runs)
            address = item.address or ''
            if address:
                out.append(f'<a href="{_html.escape(address, quote=True)}">{inner or _html.escape(item.text or "")}</a>')
            else:
                out.append(inner)
        else:  # Run
            out.append(_run_html(item))

    rendered = ''.join(out).strip()
    return rendered or _html.escape(paragraph.text.strip())


def _run_html(run) -> str:
    text = _html.escape(run.text or '')
    if not text:
        return ''
    if run.bold:
        text = f'<strong>{text}</strong>'
    if run.italic:
        text = f'<em>{text}</em>'
    if run.underline:
        text = f'<u>{text}</u>'
    return text


def _table_html(rows: list[list[str]]) -> str:
    body = ''.join(
        '<tr>' + ''.join(f'<td>{_html.escape(c)}</td>' for c in r) + '</tr>'
        for r in rows
    )
    return f'<table>{body}</table>'


def _cell_text(cell) -> str:
    return ' '.join(p.text.strip() for p in cell.paragraphs if p.text.strip())


# --------------------------------------------------------------------------- #
# Structure detection
# --------------------------------------------------------------------------- #

def _heading_level(paragraph) -> int | None:
    style = getattr(paragraph.style, 'name', '') or ''
    if style.strip().lower() == 'title':
        return 1
    match = _HEADING_RE.match(style)
    if match:
        return min(int(match.group(1)), 6)
    return None


def _count_images(paragraph) -> int:
    return len(paragraph._p.findall('.//' + qn('w:drawing'))) + \
        len(paragraph._p.findall('.//' + qn('w:pict')))


class _NumberingResolver:
    """Resolves whether a paragraph is a list item and, if so, its format
    (bullet vs number) and nesting depth. Best-effort: on any lookup failure
    it still reports the paragraph as a bullet list item."""

    def __init__(self, document):
        self._abstract_fmt: dict[str, dict[int, str]] = {}
        self._num_to_abstract: dict[str, str] = {}
        try:
            numbering = document.part.numbering_part.element
        except (AttributeError, KeyError, NotImplementedError):
            return

        for abstract in numbering.findall(qn('w:abstractNum')):
            abstract_id = abstract.get(qn('w:abstractNumId'))
            levels: dict[int, str] = {}
            for lvl in abstract.findall(qn('w:lvl')):
                ilvl = int(lvl.get(qn('w:ilvl')) or 0)
                num_fmt_el = lvl.find(qn('w:numFmt'))
                fmt = num_fmt_el.get(qn('w:val')) if num_fmt_el is not None else None
                levels[ilvl] = fmt or 'bullet'
            self._abstract_fmt[abstract_id] = levels

        for num in numbering.findall(qn('w:num')):
            num_id = num.get(qn('w:numId'))
            abstract_ref = num.find(qn('w:abstractNumId'))
            if abstract_ref is not None:
                self._num_to_abstract[num_id] = abstract_ref.get(qn('w:val'))

    def for_paragraph(self, paragraph) -> dict | None:
        pPr = paragraph._p.pPr
        if pPr is None:
            return self._by_style(paragraph)
        numPr = pPr.find(qn('w:numPr'))
        if numPr is None:
            return self._by_style(paragraph)

        ilvl_el = numPr.find(qn('w:ilvl'))
        num_id_el = numPr.find(qn('w:numId'))
        indent = int(ilvl_el.get(qn('w:val'))) if ilvl_el is not None else 0
        indent = max(0, min(indent, _MAX_INDENT))

        fmt = 'bullet'
        if num_id_el is not None:
            num_id = num_id_el.get(qn('w:val'))
            abstract_id = self._num_to_abstract.get(num_id)
            level_fmt = self._abstract_fmt.get(abstract_id, {})
            raw = level_fmt.get(indent) or level_fmt.get(0) or 'bullet'
            fmt = 'bullet' if raw == 'bullet' else 'number'
        return {'format': fmt, 'indent': indent}

    @staticmethod
    def _by_style(paragraph) -> dict | None:
        style = (getattr(paragraph.style, 'name', '') or '').strip().lower()
        if style.startswith('list bullet'):
            return {'format': 'bullet', 'indent': 0}
        if style.startswith('list number'):
            return {'format': 'number', 'indent': 0}
        return None


# --------------------------------------------------------------------------- #
# Block -> value converters (used by the apply step)
# --------------------------------------------------------------------------- #

_TAG_TO_MD = [
    (re.compile(r'</?strong>'), '**'),
    (re.compile(r'</?em>'), '*'),
    (re.compile(r'</?u>'), ''),
]
_LINK_RE = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.DOTALL)


def _inline_markdown(block: dict) -> str:
    raw = block.get('html') or _html.escape(block.get('text', ''))
    raw = _LINK_RE.sub(lambda m: f'[{m.group(2)}]({_html.unescape(m.group(1))})', raw)
    for pattern, repl in _TAG_TO_MD:
        raw = pattern.sub(repl, raw)
    raw = re.sub(r'<[^>]+>', '', raw)
    return _html.unescape(raw).strip()


def blocks_to_markdown(blocks: list[dict]) -> str:
    """Join blocks into a Markdown string (for overview / topic summary)."""
    lines: list[str] = []
    for block in blocks:
        kind = block.get('kind')
        if kind == 'heading':
            level = block.get('level') or 2
            lines.append('#' * min(level + 1, 6) + ' ' + block.get('text', '').strip())
        elif kind == 'list_item':
            indent = '  ' * int(block.get('indent') or 0)
            marker = '1.' if block.get('list') == 'number' else '-'
            lines.append(f'{indent}{marker} {_inline_markdown(block)}')
        elif kind == 'table':
            for row in block.get('rows', []):
                lines.append('- ' + ' | '.join(c.strip() for c in row))
        else:
            lines.append(_inline_markdown(block))
        lines.append('')
    return '\n'.join(lines).strip()


def blocks_to_items(blocks: list[dict]) -> list[str]:
    """Flatten blocks into a list of strings (for a topic's `items`)."""
    items: list[str] = []
    for block in blocks:
        if block.get('kind') == 'table':
            items.extend(' | '.join(c.strip() for c in row) for row in block.get('rows', []))
        else:
            value = _inline_markdown(block)
            if value:
                items.append(value)
    return items


def blocks_to_text(blocks: list[dict]) -> str:
    """Join blocks into a single plain-text line (title / path / audience)."""
    return ' '.join(block.get('text', '').strip() for block in blocks if block.get('text', '').strip()).strip()
