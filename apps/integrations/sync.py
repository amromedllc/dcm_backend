"""
TherapyPMS → DCM sync service.

Appointments and patients are loaded live from the TherapyPMS iOS API.
The direct therapypms database connection has been removed — bulk client
import now goes through list_patients + upsert in clients.api.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

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


def sync_tpms_clients(admin_id: int | None = None) -> SyncResult:
    """
    Deprecated: TherapyPMS DB access removed.

    Clients are upserted on each GET /clients via the iOS patient list API.
    """
    result = SyncResult()
    result.errors.append(
        'sync_tpms_clients is unavailable — TherapyPMS database was removed. '
        'Use GET /clients (live TPMS patient list) instead.'
    )
    logger.warning(result.errors[0])
    return result
