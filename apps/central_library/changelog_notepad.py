"""Import bundled release notes as drafts, preserving reviewed and published copy."""
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from apps.central_library.management.commands.import_changelog_notepad import parse_notepad as parse_release_notes

NOTEPAD_PATH = Path(__file__).with_name('data') / 'changelog-notepad.md'


def _without_developer_fixes(body):
    public_lines = []
    hidden = False
    for line in body.splitlines():
        if line.strip() == '<!-- developer-fix -->':
            if hidden:
                raise ValueError('Developer-fix blocks cannot be nested')
            hidden = True
        elif line.strip() == '<!-- /developer-fix -->':
            if not hidden:
                raise ValueError('Unexpected developer-fix closing marker')
            hidden = False
        elif not hidden:
            public_lines.append(line)
    if hidden:
        raise ValueError('Developer-fix block needs a closing marker')
    return '\n'.join(public_lines)


def parse_notepad(text):
    entries = []
    source_ids = set()
    for block in re.split(r'^## ', text, flags=re.M)[1:]:
        heading, _, body = block.partition('\n')
        parts = heading.split(' | ')
        if len(parts) != 3:
            raise ValueError('Expected heading: ## source-id | YYYY-MM-DD | Title')
        source_id, release_date, title = parts
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', source_id) or len(source_id) > 80:
            raise ValueError('Source IDs must be lowercase slugs of at most 80 characters')
        if source_id in source_ids or not title.strip() or len(title.strip()) > 200:
            raise ValueError('Source IDs must be unique and titles must contain 1–200 characters')
        date.fromisoformat(release_date)
        source_ids.add(source_id)
        body = _without_developer_fixes(body)
        statuses = re.findall(r'^Status:\s*(.*?)\s*$', body, flags=re.M)
        if len(statuses) > 1 or (statuses and statuses[0].casefold() not in ('draft', 'published', 'hidden')):
            raise ValueError('Use one Status: Draft, Published, or Hidden per entry')
        if statuses and statuses[0].casefold() == 'hidden':
            continue
        parsed = parse_release_notes(f'## {release_date} — {title}\n{body}')
        if len(parsed) != 1 or not parsed[0]['body']:
            raise ValueError('Each release note needs a section and bullet text')
        entries.append({**parsed[0], 'source_id': source_id, 'is_published': False})
    if not source_ids:
        raise ValueError('No changelog entries found')
    return entries


class _BodyText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        self.handle_starttag(tag, [])


def _normalized_text(value):
    return ' '.join(value.split()).casefold()


def _body_key(body):
    parser = _BodyText()
    parser.feed(body)
    return _normalized_text(''.join(parser.parts))


def import_notepad(user=None):
    from django.db import transaction
    from django_tenants.utils import get_public_schema_name, schema_context
    from apps.central_library.models import ChangelogEntry

    entries = parse_notepad(NOTEPAD_PATH.read_text(encoding='utf-8'))
    created = 0
    updated = 0
    with schema_context(get_public_schema_name()), transaction.atomic():
        existing = list(ChangelogEntry.objects.values('id', 'source_id', 'title', 'release_date', 'body', 'version'))
        source_ids = {row['source_id'] for row in existing if row['source_id']}
        by_source = {row['source_id']: row for row in existing if row['source_id']}

        def fill_version(row, entry):
            previous_date_version = 'v' + entry['release_date'].strftime('%Y.%m.%d')
            # Only the 34 historical groups were assigned the initial v1 sequence.
            historical_version = re.fullmatch(r'v2\.(\d+)\.0', entry['version'])
            previous_sequence_version = None
            if historical_version and 10 <= int(historical_version[1]) <= 43:
                previous_sequence_version = f'v1.{int(historical_version[1]) - 10}.0'
            replace_legacy_version = (
                row['version'] in (previous_date_version, previous_sequence_version)
                and re.fullmatch(r'v\d+\.\d+\.\d+', entry['version'])
                and entry['version'] != previous_date_version
            )
            if not entry['version'] or (row['version'].strip() and not replace_legacy_version):
                return 0
            count = ChangelogEntry.objects.filter(pk=row['id'], version=row['version']).update(version=entry['version'])
            row['version'] = entry['version']
            return count
        by_title = {}
        by_body = {}
        for row in existing:
            title_key = _normalized_text(row['title'])
            body_key = _body_key(row['body'])
            if title_key:
                by_title.setdefault(title_key, row)
            if body_key:
                by_body.setdefault(body_key, row)
        for entry in entries:
            source_id = entry['source_id']
            if source_id in source_ids:
                if source_id in by_source:
                    updated += fill_version(by_source[source_id], entry)
                continue
            title_key = _normalized_text(entry['title'])
            body_key = _body_key(entry['body'])
            match = by_title.get(title_key) or (by_body.get(body_key) if body_key else None)
            if match is not None:
                updated += fill_version(match, entry)
                # Link legacy/manual notes without changing reviewed content or publication.
                if not match['source_id']:
                    ChangelogEntry.objects.filter(
                        pk=match['id'], source_id=match['source_id'],
                    ).update(source_id=source_id)
                    match['source_id'] = source_id
                source_ids.add(source_id)
                continue
            defaults = {key: value for key, value in entry.items() if key != 'source_id'}
            saved, added = ChangelogEntry.objects.get_or_create(
                source_id=source_id, defaults={**defaults, 'created_by': user},
            )
            source_ids.add(source_id)
            row = {**entry, 'id': saved.pk}
            by_source[source_id] = row
            by_title[title_key] = row
            if body_key:
                by_body[body_key] = row
            created += int(added)
    return {'created': created, 'updated': updated, 'skipped': len(entries) - created - updated}
