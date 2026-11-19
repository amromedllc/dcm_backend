"""Content normalization and retention of selections, not a vendor catalog cache."""
import hashlib
import json
from dataclasses import asdict

NORMALIZATION_VERSION = 1


def normalized_content(content):
    payload = asdict(content)
    # JSON normalization produces a detached structure (tuples become lists).
    return json.loads(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def content_digest(payload):
    body = json.dumps({'normalization_version': NORMALIZATION_VERSION, 'content': payload},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def retain_selection(content):
    from apps.integrations.models import PlannerContentSnapshot
    from shared.tenancy import current_org_id
    payload = normalized_content(content)
    values = {key: payload[key] for key in (
        'provider', 'planner_id', 'component_id', 'category', 'text', 'edition',
        'version', 'language', 'attribution',
    )}
    snapshot, _ = PlannerContentSnapshot.objects.get_or_create(
        organization_id=current_org_id(), digest=content_digest(payload),
        defaults={**values, 'normalized_payload': payload, 'normalization_version': NORMALIZATION_VERSION},
    )
    return snapshot
