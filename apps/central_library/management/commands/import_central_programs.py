import csv
import io
import json
import re
import zipfile
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.central_library.models import CentralProgram, CentralProgramFolder


CATEGORY_MAP = {
    'skill_acquisition': CentralProgram.Category.SKILL_ACQUISITION,
    'skill acquisition': CentralProgram.Category.SKILL_ACQUISITION,
    'skill': CentralProgram.Category.SKILL_ACQUISITION,
    'behavior_reduction': CentralProgram.Category.BEHAVIOR_REDUCTION,
    'behavior reduction': CentralProgram.Category.BEHAVIOR_REDUCTION,
    'behavior': CentralProgram.Category.BEHAVIOR_REDUCTION,
    'abc_recording': CentralProgram.Category.ABC_RECORDING,
    'abc recording': CentralProgram.Category.ABC_RECORDING,
    'abc': CentralProgram.Category.ABC_RECORDING,
    'telehealth': CentralProgram.Category.TELEHEALTH,
    'assessment': CentralProgram.Category.ASSESSMENT,
}

EXTRA_COLUMNS = [
    'default_measurement',
    'alternative_measurements',
    'response_options',
    'measurement_unit',
    'setting_options',
]

REQUIRED_COLUMNS = {'program_name'}


def clean(value):
    return str(value or '').strip()


def normalize_header(value):
    return clean(value).lower().replace(' ', '_')


def split_list(value):
    raw = clean(value)
    if not raw:
        return []
    delimiter = '|' if '|' in raw else ','
    return [part.strip() for part in raw.split(delimiter) if part.strip()]


def normalize_category(value):
    raw = clean(value).lower()
    return CATEGORY_MAP.get(raw, CentralProgram.Category.SKILL_ACQUISITION)


def parse_json_cell(value):
    raw = clean(value)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_prompt_levels(value):
    parsed = parse_json_cell(value)
    if isinstance(parsed, list):
        levels = []
        for index, item in enumerate(parsed):
            if isinstance(item, str):
                label = item.strip()
                level = {'label': label, 'score': index, 'abbreviation': label[:3].upper()}
            elif isinstance(item, dict):
                label = clean(item.get('label') or item.get('name'))
                if not label:
                    continue
                level = {
                    'label': label,
                    'score': int(item.get('score', index) or 0),
                    'abbreviation': clean(item.get('abbreviation')) or label[:3].upper(),
                    'is_success': bool(item.get('is_success', False)),
                }
                if item.get('color'):
                    level['color'] = clean(item.get('color'))
            else:
                continue
            levels.append(level)
        if levels and not any(level.get('is_success') for level in levels):
            levels[0]['is_success'] = True
        return levels

    labels = re.split(r'[|,\n;]+', clean(value))
    labels = [label.strip() for label in labels if label.strip()]
    if not labels:
        return []

    success_index = 0
    for index, label in enumerate(labels):
        if label.lower() in {'independent', 'success', 'successful', 'correct'}:
            success_index = index
            break

    return [
        {
            'label': label,
            'score': index,
            'abbreviation': ''.join(part[0] for part in label.split()[:3]).upper() or label[:3].upper(),
            'is_success': index == success_index,
        }
        for index, label in enumerate(labels)
    ]


def csv_rows(path):
    raw = path.read_bytes()
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise CommandError('Could not decode CSV. Save the file as UTF-8 CSV and try again.')
    return list(csv.DictReader(io.StringIO(text)))


def column_index(cell_ref):
    letters = ''.join(re.findall(r'[A-Z]+', cell_ref or ''))
    total = 0
    for letter in letters:
        total = total * 26 + (ord(letter) - ord('A') + 1)
    return max(total - 1, 0)


def cell_text(cell, ns, shared_strings):
    if cell.attrib.get('t') == 'inlineStr':
        return ''.join(node.text or '' for node in cell.findall('.//x:t', ns))
    value = cell.find('x:v', ns)
    text = value.text if value is not None else ''
    if cell.attrib.get('t') == 's' and text:
        return shared_strings[int(text)]
    return text or ''


def xlsx_rows(path):
    with zipfile.ZipFile(path) as archive:
        shared_strings = []
        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
            for si in root.findall('x:si', ns):
                shared_strings.append(''.join(node.text or '' for node in si.findall('.//x:t', ns)))

        sheet_name = 'xl/worksheets/sheet1.xml'
        if sheet_name not in archive.namelist():
            raise CommandError('XLSX file does not contain xl/worksheets/sheet1.xml.')
        root = ElementTree.fromstring(archive.read(sheet_name))
        matrix = []
        for row in root.findall('.//x:sheetData/x:row', ns):
            values = []
            for cell in row.findall('x:c', ns):
                idx = column_index(cell.attrib.get('r'))
                while len(values) <= idx:
                    values.append('')
                values[idx] = cell_text(cell, ns, shared_strings)
            matrix.append(values)
    if not matrix:
        return []
    headers = [normalize_header(header) for header in matrix[0]]
    return [dict(zip(headers, row)) for row in matrix[1:] if any(clean(cell) for cell in row)]


def read_rows(path):
    suffix = path.suffix.lower()
    if suffix == '.csv':
        rows = csv_rows(path)
    elif suffix == '.xlsx':
        rows = xlsx_rows(path)
    else:
        raise CommandError('Only .csv and .xlsx files are supported.')
    return [{normalize_header(key): value for key, value in row.items()} for row in rows]


def append_import_notes(instructions, row):
    extras = {key: clean(row.get(key)) for key in EXTRA_COLUMNS if clean(row.get(key))}
    if not extras:
        return clean(instructions)
    lines = [clean(instructions)] if clean(instructions) else []
    lines.append('Import metadata:')
    for key, value in extras.items():
        lines.append(f'{key}: {value}')
    return '\n'.join(lines)


def parse_program_rows(rows):
    if not rows:
        raise CommandError('No data rows found.')

    missing = REQUIRED_COLUMNS - set(rows[0])
    if missing:
        raise CommandError(f'Missing required column(s): {", ".join(sorted(missing))}')

    parsed_rows = []
    for row_number, row in enumerate(rows, start=2):
        name = clean(row.get('program_name'))
        if not name:
            raise CommandError(f'Row {row_number}: program_name is required.')
        parsed_rows.append({
            'row_number': row_number,
            'program_id': clean(row.get('program_id')),
            'name': name,
            'category': normalize_category(row.get('program_type')),
            'treatment_area': clean(row.get('clinical_domain')),
            'instructions': append_import_notes('', row),
            'prompting_levels': parse_prompt_levels(row.get('prompt_levels')),
        })
    return parsed_rows


def import_rows(parsed_rows, *, folder_name, replace, user):
    created = updated = skipped = 0
    with transaction.atomic():
        folder, _ = CentralProgramFolder.objects.get_or_create(
            name=folder_name,
            defaults={'created_by': user},
        )
        for index, row in enumerate(parsed_rows):
            existing = None
            if row['program_id'].isdigit():
                existing = CentralProgram.objects.filter(id=int(row['program_id'])).first()
            if existing is None:
                existing = CentralProgram.objects.filter(folder=folder, name=row['name']).first()

            defaults = {
                'name': row['name'],
                'category': row['category'],
                'phase': CentralProgram.Phase.TEACHING,
                'status': CentralProgram.Status.ACTIVE,
                'treatment_area': row['treatment_area'],
                'instructions': row['instructions'],
                'prompting_levels': row['prompting_levels'],
                'folder': folder,
                'display_order': index * 10,
            }
            if existing and not replace:
                skipped += 1
                continue
            if existing:
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.save()
                updated += 1
            else:
                CentralProgram.objects.create(created_by=user, **defaults)
                created += 1
    return {
        'created': created,
        'updated': updated,
        'skipped': skipped,
        'folder_id': folder.id,
        'folder_name': folder.name,
    }


def import_file(path, *, folder_name, replace, user):
    from pathlib import Path

    rows = read_rows(Path(path))
    parsed_rows = parse_program_rows(rows)
    return import_rows(parsed_rows, folder_name=folder_name, replace=replace, user=user)


class Command(BaseCommand):
    help = 'Import shared Central Library programs from a .csv or .xlsx file.'

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the .csv or .xlsx file to import.')
        parser.add_argument('--folder', default='Manual Programs', help='Central Library folder name to create/use.')
        parser.add_argument('--replace', action='store_true', help='Replace/update existing rows matched by program_id or name.')
        parser.add_argument('--created-by-email', default='', help='Optional user email to store as created_by on new programs/folder.')
        parser.add_argument('--dry-run', action='store_true', help='Validate and report without writing rows.')

    def handle(self, *args, **options):
        from pathlib import Path

        path = Path(options['file'])
        if not path.exists():
            raise CommandError(f'File not found: {path}')

        rows = read_rows(path)

        user = None
        if options['created_by_email']:
            User = get_user_model()
            try:
                user = User.objects.get(email=options['created_by_email'])
            except User.DoesNotExist:
                raise CommandError(f'No user found for --created-by-email={options["created_by_email"]}')

        parsed_rows = parse_program_rows(rows)

        if options['dry_run']:
            self.stdout.write(f'Validated {len(parsed_rows)} row(s). No changes written.')
            return

        result = import_rows(parsed_rows, folder_name=options['folder'], replace=options['replace'], user=user)

        self.stdout.write(self.style.SUCCESS(
            f'Imported central programs: {result["created"]} created, {result["updated"]} updated, '
            f'{result["skipped"]} skipped.'
        ))
