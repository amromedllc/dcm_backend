"""
Import release notes from docs/changelog-notepad.md into ChangelogEntry.

Usage:
    python manage.py import_changelog_notepad
    python manage.py import_changelog_notepad --clear
    python manage.py import_changelog_notepad --publish
    python manage.py import_changelog_notepad --path /absolute/path/to/changelog-notepad.md
"""
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from apps.central_library.models import ChangelogEntry
from shared.html_sanitize import sanitize_kb_html

HEADING_RE = re.compile(r'^##\s+(\d{4}-\d{2}-\d{2})\s+[—–-]\s+(.+?)\s*$')
STATUS_RE = re.compile(r'^Status:\s*(Draft|Published)\s*$', re.I)
SECTION_RE = re.compile(
    r'^\*\*(New Features|Improvements|Fixed Issues|Known limitations(?:\s*\([^)]*\))?)\*\*\s*$',
    re.I,
)
BOLD_RE = re.compile(r'\*\*(.+?)\*\*')


def _inline_md(text: str) -> str:
    escaped = html.escape(text)
    return BOLD_RE.sub(r'<strong>\1</strong>', escaped)


def _section_html(title: str, bullets: list[str]) -> str:
    items = ''.join(f'<li>{_inline_md(b)}</li>' for b in bullets)
    return f'<h3>{html.escape(title)}</h3><ul>{items}</ul>'


def parse_notepad(text: str) -> list[dict]:
    """Parse changelog-notepad.md into entry dicts."""
    lines = text.splitlines()
    entries: list[dict] = []
    i = 0

    while i < len(lines):
        heading = HEADING_RE.match(lines[i].strip())
        if not heading:
            i += 1
            continue

        release_date = date.fromisoformat(heading.group(1))
        title = heading.group(2).strip()
        i += 1

        status = 'Draft'
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines):
            status_match = STATUS_RE.match(lines[i].strip())
            if status_match:
                status = status_match.group(1).capitalize()
                i += 1

        sections: list[tuple[str, list[str]]] = []
        current_title: str | None = None
        current_bullets: list[str] = []

        def flush():
            nonlocal current_title, current_bullets
            if current_title and current_bullets:
                sections.append((current_title, current_bullets))
            current_title = None
            current_bullets = []

        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if HEADING_RE.match(stripped):
                break
            if stripped == '---':
                i += 1
                break

            section = SECTION_RE.match(stripped)
            if section:
                flush()
                current_title = section.group(1)
                i += 1
                continue

            if stripped.startswith('- '):
                if current_title is None:
                    current_title = 'Updates'
                current_bullets.append(stripped[2:].strip())
                i += 1
                continue

            if not stripped:
                i += 1
                continue

            # Non-bullet prose under a section — keep as a list item.
            if current_title is not None:
                current_bullets.append(stripped)
            i += 1

        flush()

        body_parts = [_section_html(sec_title, bullets) for sec_title, bullets in sections]
        entries.append({
            'title': title[:200],
            'release_date': release_date,
            'body': sanitize_kb_html(''.join(body_parts)),
            'is_published': status.lower() == 'published',
            'version': '',
        })

    return entries


def default_notepad_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / 'docs' / 'changelog-notepad.md'
        if candidate.is_file():
            return candidate
    return here.parents[6] / 'docs' / 'changelog-notepad.md'


class Command(BaseCommand):
    help = 'Import docs/changelog-notepad.md into ChangelogEntry rows'

    def add_arguments(self, parser):
        parser.add_argument(
            '--path',
            default=None,
            help='Path to changelog-notepad.md (defaults to docs/changelog-notepad.md)',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete all existing changelog entries before import',
        )
        parser.add_argument(
            '--publish',
            action='store_true',
            help='Force every imported entry to published (ignore notepad Status)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to the database',
        )

    def handle(self, *args, **options):
        path = Path(options['path'] or default_notepad_path())
        if not path.is_file():
            raise CommandError(f'Notepad not found: {path}')

        entries = parse_notepad(path.read_text(encoding='utf-8'))
        if not entries:
            raise CommandError(f'No entries parsed from {path}')

        if options['publish']:
            for entry in entries:
                entry['is_published'] = True

        self.stdout.write(f'Parsed {len(entries)} entries from {path}')
        for entry in entries[:3]:
            self.stdout.write(f'  - {entry["release_date"]} — {entry["title"]} ({"Published" if entry["is_published"] else "Draft"})')
        if len(entries) > 3:
            self.stdout.write(f'  … and {len(entries) - 3} more')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — no database changes'))
            return

        with schema_context(get_public_schema_name()):
            if options['clear']:
                deleted, _ = ChangelogEntry.objects.all().delete()
                self.stdout.write(self.style.WARNING(f'Cleared {deleted} existing entries'))

            created = ChangelogEntry.objects.bulk_create([
                ChangelogEntry(**entry) for entry in entries
            ])
            published = sum(1 for e in created if e.is_published)
            drafts = len(created) - published
            self.stdout.write(self.style.SUCCESS(
                f'Imported {len(created)} changelog entries ({published} published, {drafts} drafts)'
            ))
