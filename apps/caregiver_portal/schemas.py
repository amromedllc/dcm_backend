from datetime import date, datetime
from ninja import Schema


class CaregiverAppointmentSchema(Schema):
    """
    Defensive projection of a TPMS /client-portal/appointment row — every
    field optional. The real response shape was unconfirmed as of writing
    (only the request shape was tested), so _project_appointment() in
    api.py tries several candidate key names per field and degrades to
    None rather than raising, and this schema mirrors that by making
    everything optional rather than asserting a shape that might be wrong.
    """
    id: str | None = None
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    provider_name: str | None = None
    status: str | None = None
    service_type: str | None = None
    location: str | None = None
    notes: str | None = None


class CaregiverNoteListItem(Schema):
    id: int
    note_date: date
    template_name: str | None = None
    status: str
    created_at: datetime


class CaregiverNoteDetail(CaregiverNoteListItem):
    body: dict
    updated_at: datetime
