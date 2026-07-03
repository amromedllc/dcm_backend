from datetime import date, datetime
from ninja import Schema


class ClientSchema(Schema):
    id: int
    external_id: str
    first_name: str
    last_name: str
    preferred_name: str
    full_name: str
    date_of_birth: date | None
    status: str
    intake_date: date | None
    discharge_date: date | None
    created_at: datetime
    updated_at: datetime


class ClientCreateRequest(Schema):
    first_name: str
    last_name: str
    preferred_name: str = ''
    external_id: str = ''
    date_of_birth: date | None = None
    status: str = 'active'
    intake_date: date | None = None


class ClientUpdateRequest(Schema):
    first_name: str | None = None
    last_name: str | None = None
    preferred_name: str | None = None
    external_id: str | None = None
    date_of_birth: date | None = None
    status: str | None = None
    intake_date: date | None = None
    discharge_date: date | None = None
    internal_notes: str | None = None


class StaffAssignmentSchema(Schema):
    id: int
    client_id: int
    user_id: int
    is_primary: bool
    is_active: bool
    assigned_at: datetime


class AddStaffAssignmentRequest(Schema):
    user_id: int
    is_primary: bool = False
