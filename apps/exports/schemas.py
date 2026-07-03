from datetime import date, datetime
from typing import Any
from ninja import Schema


class ExportSchema(Schema):
    id: int
    export_type: str
    status: str
    params: dict[str, Any]
    file_size_bytes: int | None
    row_count: int | None
    error_message: str
    generated_at: datetime | None
    expires_at: datetime | None
    download_count: int
    last_downloaded_at: datetime | None
    created_at: datetime


class ExportCreateRequest(Schema):
    export_type: str
    # Common params — only those relevant to the export_type are used
    client_id: int | None = None
    program_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    target_ids: list[int] = []
    note_id: int | None = None


class ExportDownloadResponse(Schema):
    export_id: int
    download_url: str
    expires_in_seconds: int | None
