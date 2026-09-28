"""Session-note PDF: a clinical document layout (header, client/session details, the note as the
author filled it in, signatures, confidentiality footer).

A template's body is rendered the way it looks on screen: its text stays, each field badge is replaced
by the value entered, and each dynamic-detail badge by its resolved value. Templates without a body
fall back to a label/value list."""
from __future__ import annotations

import io
import re
from pathlib import Path
from html.parser import HTMLParser
from xml.sax.saxutils import escape

from django.utils import timezone

BRAND = '#2563eb'
INK = '#0f172a'
MUTED = '#64748b'
RULE = '#e2e8f0'
PANEL = '#f8fafc'

_INLINE_OK = {'b', 'strong', 'i', 'em', 'u'}
_BLOCK = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'li'}


_FONT_DIR = Path(__file__).parent / 'fonts'
_fonts_ready = False


def _register_fonts() -> tuple[str, str, str]:
    """Inter (the app's own typeface), embedded so the PDF looks identical everywhere.
    Falls back to built-in Helvetica if the font files are missing. Returns (regular, semibold, bold)."""
    global _fonts_ready
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        if not _fonts_ready:
            for name, file in (('Inter', 'Inter-Regular'), ('Inter-Bold', 'Inter-Bold'), ('Inter-Italic', 'Inter-Italic'),
                               ('Inter-BoldItalic', 'Inter-BoldItalic'), ('Inter-SemiBold', 'Inter-SemiBold')):
                pdfmetrics.registerFont(TTFont(name, str(_FONT_DIR / f'{file}.ttf')))
            pdfmetrics.registerFontFamily('Inter', normal='Inter', bold='Inter-Bold', italic='Inter-Italic', boldItalic='Inter-BoldItalic')
            _fonts_ready = True
        return 'Inter', 'Inter-SemiBold', 'Inter-Bold'
    except Exception:
        return 'Helvetica', 'Helvetica-Bold', 'Helvetica-Bold'


def display_value(raw) -> str:
    if raw is None or raw == '':
        return ''
    if isinstance(raw, bool):
        return 'Yes' if raw else 'No'
    if isinstance(raw, list):
        return ', '.join(str(v) for v in raw)
    return str(raw)


class _BodyParser(HTMLParser):
    """Turns a template body into a list of blocks: ('p', markup, kind) or ('table', rows)."""

    def __init__(self, fields: dict, values: dict, tokens: dict):
        super().__init__(convert_charrefs=True)
        self.fields, self.values, self.tokens = fields, values, tokens
        self.blocks: list[tuple] = []
        self._buf: list[str] = []
        self._kind = 'p'
        self._skip_span = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._list_stack: list[dict] = []

    # -- helpers
    def _sink(self) -> list[str]:
        return self._cell if self._cell is not None else self._buf

    def _flush(self):
        if self._cell is not None:
            return
        markup = ''.join(self._buf).strip()
        if re.sub(r'(<br/>|\s)', '', markup):
            self.blocks.append(('p', markup, self._kind))
        self._buf, self._kind = [], 'p'

    # -- parser hooks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'span' and (a.get('data-custom-field') == 'true' or a.get('data-dynamic-field') == 'true'):
            key = a.get('data-key', '')
            if a.get('data-custom-field') == 'true':
                text = display_value(self.values.get(key)) or '—'
                field_type = (self.fields.get(key) or {}).get('type')
                text = escape(text).replace('\n', '<br/>')
                self._sink().append(f'<b>{text}</b>' if field_type != 'textarea' else text)
            else:
                text = self.tokens.get(key, '')
                self._sink().append(f'<b>{escape(text).replace(chr(10), "<br/>")}</b>' if text else '—')
            self._skip_span = 1
            return
        if self._skip_span:
            self._skip_span += 1
            return
        if tag in _BLOCK:
            self._flush()
            self._kind = tag if tag in ('h1', 'h2', 'h3', 'h4') else 'p'
            if tag == 'li':
                depth = len(self._list_stack)
                top = self._list_stack[-1] if self._list_stack else {'ordered': False, 'n': 0}
                top['n'] += 1
                bullet = f'{top["n"]}. ' if top['ordered'] else '• '
                self._buf.append('&nbsp;' * (4 * max(depth, 1)) + bullet)
        elif tag in ('ul', 'ol'):
            self._flush()
            self._list_stack.append({'ordered': tag == 'ol', 'n': 0})
        elif tag == 'br':
            self._sink().append('<br/>')
        elif tag in _INLINE_OK:
            self._sink().append({'strong': '<b>', 'em': '<i>'}.get(tag, f'<{tag}>'))
        elif tag == 'table':
            self._flush()
            self._table = []
        elif tag == 'tr' and self._table is not None:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if self._skip_span:
            if tag == 'span':
                self._skip_span -= 1
            return
        if tag in _BLOCK:
            self._flush()
        elif tag in ('ul', 'ol') and self._list_stack:
            self._flush()
            self._list_stack.pop()
        elif tag in _INLINE_OK:
            self._sink().append({'strong': '</b>', 'em': '</i>'}.get(tag, f'</{tag}>'))
        elif tag in ('td', 'th') and self._cell is not None and self._row is not None:
            self._row.append(''.join(self._cell).strip())
            self._cell = None
        elif tag == 'tr' and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == 'table' and self._table is not None:
            if self._table:
                self.blocks.append(('table', self._table))
            self._table = None

    def handle_data(self, data):
        if self._skip_span:
            return
        self._sink().append(escape(data))

    def close(self):
        super().close()
        self._flush()


def body_blocks(template, body: dict, tokens: dict) -> list[tuple]:
    fields = {f.get('key'): f for f in (template.fields or [])}
    parser = _BodyParser(fields, body or {}, tokens)
    parser.feed(template.body_template)
    parser.close()
    return parser.blocks


def _client_details(note) -> tuple[str, str]:
    from apps.clients.models import Client

    client = None
    if note.external_client_id is not None:
        client = (
            Client.objects.filter(id=note.external_client_id).first()
            or Client.objects.filter(external_id=str(note.external_client_id)).first()
        )
    if not client:
        return f'Client #{note.external_client_id}', ''
    dob = client.date_of_birth.strftime('%b %d, %Y') if client.date_of_birth else ''
    return client.full_name, dob


def render_note_pdf(note) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    from apps.notes.services import resolve_template_tokens

    hexc = colors.HexColor
    reg, semi, bold = _register_fonts()
    italic = 'Inter-Italic' if reg == 'Inter' else 'Helvetica-Oblique'
    styles = getSampleStyleSheet()
    normal = ParagraphStyle('N', parent=styles['Normal'], fontName=reg, fontSize=10, leading=15, textColor=hexc(INK), alignment=TA_LEFT)
    small = ParagraphStyle('S', parent=normal, fontSize=8.5, leading=12, textColor=hexc(MUTED))
    label = ParagraphStyle('L', parent=small, fontName=semi, fontSize=7, leading=9, textColor=hexc(MUTED), spaceAfter=2)
    detail = ParagraphStyle('D', parent=normal, fontName=semi, fontSize=10.5, leading=13)
    h_styles = {
        'h1': ParagraphStyle('H1', parent=normal, fontName=bold, fontSize=15, leading=19, spaceBefore=12, spaceAfter=5),
        'h2': ParagraphStyle('H2', parent=normal, fontName=bold, fontSize=12.5, leading=17, spaceBefore=12, spaceAfter=4, textColor=hexc(BRAND)),
        'h3': ParagraphStyle('H3', parent=normal, fontName=semi, fontSize=11, leading=15, spaceBefore=9, spaceAfter=3),
        'h4': ParagraphStyle('H4', parent=normal, fontName=semi, fontSize=10, leading=14, spaceBefore=7, spaceAfter=2),
    }
    para = ParagraphStyle('P', parent=normal, spaceAfter=6)
    field_label = ParagraphStyle('FL', parent=small, fontName=semi, fontSize=8, leading=11, textColor=hexc(BRAND), spaceBefore=10, spaceAfter=3)

    org_name = getattr(getattr(note, 'organization', None), 'name', '') or 'Progressly'
    template = note.template
    title = template.name if template else 'Session Note'
    client_name, dob = _client_details(note)
    staff = note.staff
    staff_name = (staff.full_name if staff else '') or '—'
    tokens = resolve_template_tokens(note) if template and template.body_template else {}

    doc_width = letter[0] - 1.5 * inch
    pad = 10

    def page_frame(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(hexc(BRAND))
        canvas.rect(0, letter[1] - 0.12 * inch, letter[0], 0.12 * inch, stroke=0, fill=1)
        canvas.setStrokeColor(hexc(RULE))
        canvas.setLineWidth(0.6)
        canvas.line(0.75 * inch, 0.72 * inch, letter[0] - 0.75 * inch, 0.72 * inch)
        canvas.setFont(reg, 7)
        canvas.setFillColor(hexc(MUTED))
        canvas.drawString(0.75 * inch, 0.55 * inch, 'CONFIDENTIAL — contains protected health information. Do not share without authorization.')
        canvas.drawString(0.75 * inch, 0.42 * inch, f'Generated {timezone.localtime():%b %d, %Y %I:%M %p}')
        canvas.setFont(semi, 7.5)
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.55 * inch, f'Page {doc.page}')
        canvas.restoreState()

    buf = io.BytesIO()
    # Frame padding is zeroed so paragraphs and boxes share the same left and right edges.
    doc = BaseDocTemplate(
        buf, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.65 * inch, bottomMargin=1.0 * inch, title=title, author=org_name,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='note', frames=[frame], onPage=page_frame)])

    story: list = []

    # Header: practice on the left, document type on the right, both on one baseline
    head = Table(
        [[Paragraph(f'<font name="{bold}" size="13" color="{BRAND}">{escape(org_name)}</font>', normal),
          Paragraph(f'<para alignment="right"><font name="{semi}" size="7.5" color="{MUTED}">CLINICAL NOTE</font></para>', normal)]],
        colWidths=[doc_width * 0.7, doc_width * 0.3],
    )
    head.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, hexc(RULE)),
    ]))
    story += [head, Spacer(1, 14)]
    story.append(Paragraph(escape(title), ParagraphStyle('T', parent=normal, fontName=bold, fontSize=20, leading=25, textColor=hexc(INK))))
    story.append(Spacer(1, 12))

    # Details panel: a 3 x 2 grid in one rounded box, cells divided by hairlines
    def cell(name: str, value: str):
        return [Paragraph(name.upper(), label), Paragraph(escape(value or '—'), detail)]

    session_time = ' – '.join(x for x in (tokens.get('session.start_time'), tokens.get('session.end_time')) if x)
    if not session_time and note.session_run_id and note.session_run:
        sr = note.session_run
        session_time = ' – '.join(
            timezone.localtime(x).strftime('%-I:%M %p') for x in (sr.started_at, sr.ended_at) if x
        )
    grid = [
        [cell('Client', client_name), cell('Date of birth', dob), cell('Note date', note.note_date.strftime('%b %d, %Y'))],
        [cell('Provider', staff_name), cell('Session time', session_time), cell('Status', note.get_status_display())],
    ]
    panel = Table(grid, colWidths=[doc_width * 0.4, doc_width * 0.3, doc_width * 0.3])
    panel.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), hexc(PANEL)),
        ('BOX', (0, 0), (-1, -1), 0.8, hexc(RULE)),
        ('ROUNDEDCORNERS', [6, 6, 6, 6]),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, hexc(RULE)),
        ('LINEBEFORE', (1, 0), (-1, -1), 0.6, hexc(RULE)),
        ('LEFTPADDING', (0, 0), (-1, -1), pad + 2), ('RIGHTPADDING', (0, 0), (-1, -1), pad),
        ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story += [panel, Spacer(1, 16)]

    # Note content
    body = note.body or {}
    if template and template.body_template.strip():
        for block in body_blocks(template, body, tokens):
            if block[0] == 'table':
                rows = [[Paragraph(c or '', normal) for c in r] for r in block[1]]
                width = max(len(r) for r in rows)
                for r in rows:
                    r += [Paragraph('', normal)] * (width - len(r))
                t = Table(rows, colWidths=[doc_width / width] * width, repeatRows=0)
                t.setStyle(TableStyle([
                    ('BOX', (0, 0), (-1, -1), 0.8, hexc(RULE)),
                    ('INNERGRID', (0, 0), (-1, -1), 0.5, hexc(RULE)),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ]))
                story += [Spacer(1, 2), t, Spacer(1, 8)]
            else:
                story.append(Paragraph(block[1], h_styles.get(block[2], para)))
    elif template and template.fields:
        rows = [
            [Paragraph(escape(str(f.get('label', f.get('key')))).upper(), field_label),
             Paragraph(escape(display_value(body.get(f.get('key'))) or '—').replace('\n', '<br/>'), normal)]
            for f in template.fields
        ]
        fl = Table(rows, colWidths=[doc_width * 0.32, doc_width * 0.68], repeatRows=0)
        fl.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.8, hexc(RULE)),
            ('LINEBELOW', (0, 0), (-1, -2), 0.5, hexc(RULE)),
            ('BACKGROUND', (0, 0), (0, -1), hexc(PANEL)),
            ('LINEAFTER', (0, 0), (0, -1), 0.5, hexc(RULE)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), pad), ('RIGHTPADDING', (0, 0), (-1, -1), pad),
            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(fl)
    elif isinstance(body.get('text'), str):
        from apps.exports.tasks import _html_to_reportlab_markup
        story.append(Paragraph(_html_to_reportlab_markup(body['text']), para))
    else:
        for key, raw in body.items():
            story += [Paragraph(escape(str(key)).upper(), field_label), Paragraph(escape(display_value(raw) or '—'), para)]

    if note.rejection_reason:
        story += [Spacer(1, 10), Paragraph('REJECTION REASON', field_label),
                  Paragraph(escape(note.rejection_reason).replace('\n', '<br/>'), para)]

    # Signatures: one bordered box per signer, two across
    signatures = list(note.signatures.all())
    story += [Spacer(1, 18)]
    sig_head = Paragraph('ELECTRONIC SIGNATURES', ParagraphStyle('SH', parent=label, textColor=hexc(BRAND), fontSize=8, spaceAfter=6))
    if signatures:
        boxes = []
        for sig in signatures:
            local = timezone.localtime(sig.signed_at)
            role = f' · {escape(sig.signer_role)}' if sig.signer_role else ''
            boxes.append([
                Paragraph(f'<font name="{italic}" size="14">{escape(sig.signer_name)}</font>', normal),
                Paragraph(f'{escape(sig.get_signature_type_display())}{role}', small),
                Paragraph(f'Signed electronically {local:%b %d, %Y at %I:%M %p}', small),
            ])
        # each signer is its own bordered card
        cards = []
        for b in boxes:
            card = Table([[b[0]], [b[1]], [b[2]]], colWidths=[doc_width / 2 - 8])
            card.setStyle(TableStyle([
                ('BOX', (0, 0), (-1, -1), 0.8, hexc(RULE)), ('ROUNDEDCORNERS', [6, 6, 6, 6]),
                ('BACKGROUND', (0, 0), (-1, -1), hexc(PANEL)),
                ('LEFTPADDING', (0, 0), (-1, -1), pad + 2), ('RIGHTPADDING', (0, 0), (-1, -1), pad),
                ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, 0), 8), ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
            ]))
            cards.append(card)
        rows = [cards[i:i + 2] + [''] * (2 - len(cards[i:i + 2])) for i in range(0, len(cards), 2)]
        grid_t = Table(rows, colWidths=[doc_width / 2] * 2)
        grid_t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([sig_head, grid_t]))
    else:
        story.append(KeepTogether([sig_head, Paragraph('Not yet signed.', small)]))

    doc.build(story)
    return buf.getvalue()
