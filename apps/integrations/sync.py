"""
TherapyPMS → DCM sync service.

Appointments are read live from TPMS on every request — no sync needed.
This module only handles the initial bulk import of Client records so that
DCM has stable primary keys before any user session begins.

Usage:
    from apps.integrations.sync import sync_tpms_clients
    result = sync_tpms_clients()    # returns SyncResult
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped

    def __str__(self) -> str:
        return (
            f'created={self.created} updated={self.updated} '
            f'skipped={self.skipped} errors={len(self.errors)}'
        )


# ---------------------------------------------------------------------------
# Client sync
# ---------------------------------------------------------------------------

def sync_tpms_clients(admin_id: int | None = None) -> SyncResult:
    """
    Import / update DCM Client records from the TPMS `clients` table.

    Args:
        admin_id: Restrict to a specific TPMS admin_id (facility). If None,
                  imports all active clients.
    """
    from apps.legacy.models import TpmsClient
    from apps.clients.models import Client

    result = SyncResult()
    qs = TpmsClient.objects.using('therapypms').filter(is_active_client=1)
    if admin_id is not None:
        qs = qs.filter(admin_id=admin_id)

    for tpms in qs.iterator(chunk_size=200):
        ext_id = str(tpms.pk)
        first_name = (tpms.client_first_name or '').strip()
        last_name = (tpms.client_last_name or '').strip()

        if not first_name and not last_name:
            result.skipped += 1
            continue

        defaults: dict[str, Any] = {
            'first_name': first_name or 'Unknown',
            'last_name': last_name or 'Unknown',
            'preferred_name': (tpms.client_preferred or '').strip(),
            'date_of_birth': tpms.client_dob,
            'status': Client.Status.ACTIVE,
            'tpms_admin_id': tpms.admin_id,
        }

        try:
            client, created = Client.objects.get_or_create(
                external_id=ext_id,
                defaults=defaults,
            )
            if created:
                result.created += 1
            else:
                # Update mutable fields on every sync run
                changed = False
                for attr, val in defaults.items():
                    if getattr(client, attr) != val:
                        setattr(client, attr, val)
                        changed = True
                if changed:
                    client.save(update_fields=list(defaults.keys()))
                    result.updated += 1
                else:
                    result.skipped += 1
        except Exception as exc:
            logger.exception('Error syncing TPMS client pk=%s', tpms.pk)
            result.errors.append(f'client pk={tpms.pk}: {exc}')

    logger.info('sync_tpms_clients finished — %s', result)
    return result
