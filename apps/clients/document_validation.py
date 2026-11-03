"""Bounded file validation; files remain opaque, attachment-only downloads."""
import hashlib
import io
import re
import zlib
from PIL import Image
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from ninja.errors import HttpError

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
DOCUMENT_TYPES = {
    '.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.odt': 'application/vnd.oasis.opendocument.text',
    '.ods': 'application/vnd.oasis.opendocument.spreadsheet',
    '.odp': 'application/vnd.oasis.opendocument.presentation',
    '.txt': 'text/plain', '.csv': 'text/csv', '.rtf': 'application/rtf',
}
_OFFICE_PARTS = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml', '.pptx': 'ppt/presentation.xml'}


def _xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Document XML cannot contain entities')
    return ElementTree.fromstring(data)


def _validate_package(data, suffix):
    with ZipFile(io.BytesIO(data)) as package:
        entries = package.infolist()
        names = {entry.filename for entry in entries}
        if len(entries) > 5000 or sum(e.file_size for e in entries) > 80 * 1024 * 1024:
            raise ValueError('Document package is too large')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if entry.flag_bits & 1 or path.is_absolute() or '..' in path.parts or '\\' in entry.filename:
                raise ValueError('Invalid document package')
            if entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 200:
                raise ValueError('Document compression ratio is too high')
            lowered = entry.filename.lower()
            if lowered.endswith('.bin') or '/embeddings/' in lowered or '/scripts/' in lowered or 'vba' in lowered:
                raise ValueError('Macro and embedded executable content is not supported')
        if suffix in _OFFICE_PARTS:
            if '[Content_Types].xml' not in names or _OFFICE_PARTS[suffix] not in names:
                raise ValueError('Wrong Office document type')
            types = package.read('[Content_Types].xml')
            if b'macroenabled' in types.lower() or b'vbaproject' in types.lower():
                raise ValueError('Macro-enabled documents are not supported')
            _xml(types)
            _xml(package.read(_OFFICE_PARTS[suffix]))
        else:
            if package.read('mimetype').decode('ascii') != DOCUMENT_TYPES[suffix]:
                raise ValueError('Wrong OpenDocument type')
            _xml(package.read('content.xml'))


def validate_document(file):
    original = str(file.name or '').replace('\\', '/').rsplit('/', 1)[-1]
    original = re.sub(r'[\x00-\x1f\x7f]', '', original).strip()
    if not original or len(original) > 240:
        raise HttpError(400, 'Choose a filename of 1–240 characters')
    suffix = PurePosixPath(original).suffix.lower()
    if suffix not in DOCUMENT_TYPES:
        raise HttpError(400, 'Unsupported document format')
    # Do not rely solely on a browser-supplied size or content type.
    file.seek(0)
    data = file.read(MAX_DOCUMENT_BYTES + 1)
    file.seek(0)
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise HttpError(400, 'Document must be nonempty and no larger than 20 MB')
    try:
        if suffix in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}:
            with Image.open(io.BytesIO(data)) as image:
                expected = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.gif': 'GIF', '.webp': 'WEBP'}[suffix]
                if image.format != expected:
                    raise ValueError('Image format does not match extension')
                image.verify()
        elif suffix == '.pdf':
            if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:
                raise ValueError('Invalid PDF signature')
        elif suffix in _OFFICE_PARTS or suffix in {'.odt', '.ods', '.odp'}:
            _validate_package(data, suffix)
        elif suffix == '.rtf':
            if not data.lstrip().startswith(b'{\\rtf') or re.search(rb'\\(?:object|objdata)\b', data, re.I):
                raise ValueError('Invalid or unsupported RTF content')
        else:
            text = data.decode('utf-8-sig')
            if '\x00' in text or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
                raise ValueError('Invalid text document')
    except (ValueError, OSError, KeyError, BadZipFile, ElementTree.ParseError, UnicodeError, Image.DecompressionBombError, NotImplementedError, RuntimeError, EOFError, zlib.error):
        raise HttpError(400, 'File contents do not match a supported document format')
    finally:
        file.seek(0)
    return original, DOCUMENT_TYPES[suffix], len(data), hashlib.sha256(data).hexdigest()
