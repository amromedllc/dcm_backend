"""Shared helpers for turning uploaded content into KnowledgeBaseModule /
KnowledgeBaseTopic rows.

`split_list` / `bool_value` / `int_value` are used by both the CSV/XLSX admin
import (apps.central_library.admin) and the Word-document mapping flow
(apps.central_library.api). `apply_knowledge_base_import` is the Word flow's
commit step: it reads an author's saved block->field mapping and writes the
module + topics.
"""
from __future__ import annotations

import json

from django.db import transaction
from slugify import slugify

from shared.docx_blocks import blocks_to_items, blocks_to_markdown, blocks_to_text

from .models import KnowledgeBaseImport, KnowledgeBaseModule, KnowledgeBaseTopic


def split_list(value):
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    if text.startswith('['):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    separator = '|' if '|' in text else '\n' if '\n' in text else ','
    return [item.strip() for item in text.split(separator) if item.strip()]


def bool_value(value, default=True):
    if value in (None, ''):
        return default
    return str(value).strip().lower() not in {'0', 'false', 'no', 'n', 'inactive'}


def int_value(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Word-document mapping -> module payload
# --------------------------------------------------------------------------- #

class MappingError(ValueError):
    """Raised when a saved mapping can't produce a valid module."""


MODULE_SLOTS = ('title', 'path', 'overview', 'audience')
TOPIC_SLOTS = ('title', 'summary', 'items')
MAX_TOPICS = 12
DEFAULT_TOPICS = 3

_MODULE_SLOT_LABELS = {
    'title': 'Module · Title',
    'path': 'Module · Path',
    'overview': 'Module · Overview',
    'audience': 'Module · Audience',
}
_TOPIC_SLOT_LABELS = {'title': 'Title', 'summary': 'Summary', 'items': 'Items (bullets)'}


def target_option_groups(topic_count: int):
    """[(group_label, [(value, label), ...]), ...] for the per-block <select>."""
    groups = [('Module', [(f'module.{slot}', _MODULE_SLOT_LABELS[slot]) for slot in MODULE_SLOTS])]
    for k in range(1, max(1, topic_count) + 1):
        groups.append((
            f'Topic {k}',
            [(f'topic.{k}.{slot}', f'Topic {k} · {_TOPIC_SLOT_LABELS[slot]}') for slot in TOPIC_SLOTS],
        ))
    return groups


def empty_mapping(topic_count: int) -> dict:
    return {
        'module': {slot: [] for slot in MODULE_SLOTS},
        'topics': [
            {'key': f't{k}', **{slot: [] for slot in TOPIC_SLOTS}}
            for k in range(1, max(1, topic_count) + 1)
        ],
    }


def mapping_from_assignments(assignments: dict, blocks: list[dict], topic_count: int) -> dict:
    """assignments: {block_id: 'module.overview' | 'topic.2.items' | ''}.
    Block order is preserved so each slot's block list stays in document order.
    """
    mapping = empty_mapping(topic_count)
    for block in blocks:
        target = (assignments.get(block['id']) or '').strip()
        if not target:
            continue
        parts = target.split('.')
        if parts[0] == 'module' and len(parts) == 2 and parts[1] in MODULE_SLOTS:
            mapping['module'][parts[1]].append(block['id'])
        elif parts[0] == 'topic' and len(parts) == 3 and parts[2] in TOPIC_SLOTS:
            try:
                index = int(parts[1]) - 1
            except ValueError:
                continue
            if 0 <= index < len(mapping['topics']):
                mapping['topics'][index][parts[2]].append(block['id'])
    return mapping


def assignments_from_mapping(mapping: dict) -> dict:
    """Inverse of mapping_from_assignments: {block_id: target_string}."""
    out: dict[str, str] = {}
    module_map = (mapping or {}).get('module') or {}
    for slot in MODULE_SLOTS:
        for block_id in module_map.get(slot) or []:
            out[block_id] = f'module.{slot}'
    for index, topic_map in enumerate((mapping or {}).get('topics') or [], start=1):
        for slot in TOPIC_SLOTS:
            for block_id in topic_map.get(slot) or []:
                out[block_id] = f'topic.{index}.{slot}'
    return out


def mapping_topic_count(mapping: dict) -> int:
    count = len((mapping or {}).get('topics') or [])
    return count or DEFAULT_TOPICS


def _resolve(block_ids, blocks_by_id):
    seen = []
    for block_id in block_ids or []:
        block = blocks_by_id.get(block_id)
        if block is not None:
            seen.append(block)
    return seen


def build_module_payload(mapping: dict, blocks: list[dict], *, slug: str, icon: str,
                         display_order: int, is_active: bool) -> tuple[str, dict, list[dict]]:
    """Turn a saved `mapping` + the parsed `blocks` into
    (slug, module_defaults, [topic_dict, ...]). Raises MappingError on
    anything that would produce an unusable module.
    """
    blocks_by_id = {b['id']: b for b in blocks}
    module_map = (mapping or {}).get('module') or {}

    title = blocks_to_text(_resolve(module_map.get('title'), blocks_by_id))
    if not title:
        raise MappingError('Assign at least one block to the module title.')

    resolved_slug = (slug or '').strip() or slugify(title)[:80]
    if not resolved_slug:
        raise MappingError('Could not derive a slug — set one explicitly.')

    valid_icons = {choice[0] for choice in KnowledgeBaseModule.Icon.choices}
    if icon not in valid_icons:
        icon = KnowledgeBaseModule.Icon.BOOK

    module_defaults = {
        'title': title[:160],
        'path': blocks_to_text(_resolve(module_map.get('path'), blocks_by_id))[:240],
        'icon': icon,
        'overview': blocks_to_markdown(_resolve(module_map.get('overview'), blocks_by_id)),
        'audience': split_list(blocks_to_text(_resolve(module_map.get('audience'), blocks_by_id))),
        'display_order': int_value(display_order),
        'is_active': bool(is_active),
    }

    topics: list[dict] = []
    for index, topic_map in enumerate((mapping or {}).get('topics') or []):
        topic_title = blocks_to_text(_resolve(topic_map.get('title'), blocks_by_id))
        if not topic_title:
            continue
        topics.append({
            'title': topic_title[:180],
            'summary': blocks_to_markdown(_resolve(topic_map.get('summary'), blocks_by_id)),
            'items': blocks_to_items(_resolve(topic_map.get('items'), blocks_by_id)),
            'display_order': (index + 1) * 10,
        })

    if not topics:
        raise MappingError('Assign a title to at least one topic.')

    return resolved_slug, module_defaults, topics


@transaction.atomic
def apply_knowledge_base_import(kb_import: KnowledgeBaseImport, *, user, slug: str, icon: str,
                                display_order: int, is_active: bool,
                                replace_topics: bool) -> KnowledgeBaseModule:
    resolved_slug, module_defaults, topics = build_module_payload(
        kb_import.mapping, kb_import.blocks,
        slug=slug, icon=icon, display_order=display_order, is_active=is_active,
    )

    module, created = KnowledgeBaseModule.objects.update_or_create(
        slug=resolved_slug, defaults=module_defaults,
    )
    if created and not module.created_by_id:
        module.created_by = user
        module.save(update_fields=['created_by'])

    if replace_topics:
        module.topics.all().delete()
        KnowledgeBaseTopic.objects.bulk_create([
            KnowledgeBaseTopic(module=module, **topic) for topic in topics
        ])
    else:
        for topic in topics:
            KnowledgeBaseTopic.objects.update_or_create(
                module=module, title=topic['title'],
                defaults={k: v for k, v in topic.items() if k != 'title'},
            )

    kb_import.status = KnowledgeBaseImport.Status.APPLIED
    kb_import.target_module = module
    kb_import.save(update_fields=['status', 'target_module', 'updated_at'])
    return module
