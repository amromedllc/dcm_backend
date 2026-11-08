"""Organization/practice-scoped RBT supervision ledger and monthly reports."""
from apps.accounts.permissions import require_permission

import calendar
import csv
import io
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field, field_validator

from apps.accounts.auth import jwt_auth
from apps.accounts.api import _same_practice_q
from apps.accounts.models import User
from shared.permissions import require_roles
from shared.tenancy import current_org_id
from .models import SupervisionContact, SupervisionServiceHours

router = Router(auth=jwt_auth)


class ContactInput(Schema):
    rbt_id: int
    supervisor_id: int
    date: date
    hours: Decimal = Field(gt=0, le=24, max_digits=5, decimal_places=2)
    face_to_face: bool = True
    individual: bool = True
    observed_services: bool = False
    qualifications_confirmed: bool
    notes: str = Field(default='', max_length=10000)


class ContactOutput(ContactInput):
    id: int
    supervisor_name: str
    void_reason: str


class ServiceHoursInput(Schema):
    rbt_id: int
    month: date
    hours: Decimal = Field(ge=0, le=744, max_digits=6, decimal_places=2)

    @field_validator('month')
    @classmethod
    def first_day(cls, value):
        if value.day != 1:
            raise ValueError('Month must be the first day of the calendar month')
        return value


class VoidInput(Schema):
    reason: str = Field(min_length=1, max_length=500)


class PersonOutput(Schema):
    id: int
    name: str
    role: str


class MonthlyReport(Schema):
    rbt_id: int
    rbt_name: str
    month: date
    service_hours: Decimal | None
    supervision_hours: Decimal
    percentage: Decimal | None
    required_hours: Decimal | None
    remaining_hours: Decimal | None
    contacts: int
    individual_contacts: int
    observation_contacts: int
    recorded_requirements_met: bool


def _people(request):
    # User lives in the shared schema: require both organization and practice.
    return User.objects.filter(
        _same_practice_q(request.user), organization_id=current_org_id(),
    )


def _person(request, person_id, roles):
    person = _people(request).filter(pk=person_id, role__in=roles, is_active=True).first()
    if person is None:
        raise HttpError(404, 'Staff member not found')
    return person


def _rbts(request):
    people = _people(request).filter(role='staff')
    if request.user.role == 'staff':
        people = people.filter(pk=request.user.pk)
    return people.order_by('last_name', 'first_name')


def _contacts(request):
    people = _people(request).values('pk')
    qs = SupervisionContact.objects.filter(rbt_id__in=people, supervisor_id__in=people)
    if request.user.role == 'staff':
        qs = qs.filter(rbt_id=request.user.pk)
    return qs.select_related('supervisor')


def _month(month):
    if month.day != 1:
        raise HttpError(400, 'Month must be the first day of the calendar month')
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def _contact_output(row):
    return {**{field: getattr(row, field) for field in ContactInput.model_fields},
            'id': row.pk, 'supervisor_name': row.supervisor.full_name,
            'void_reason': row.void_reason}


@router.get('/people', response=list[PersonOutput])
@require_roles('admin', 'supervisor')
def people(request):
    require_permission(request, 'supervision_view')
    require_permission(request, 'supervision_manage')
    return [{'id': u.pk, 'name': u.full_name, 'role': u.role}
            for u in _people(request).filter(is_active=True, role__in=['staff', 'supervisor', 'admin']).order_by('last_name', 'first_name')]


@router.post('/contacts', response={201: ContactOutput})
@require_roles('admin', 'supervisor')
@transaction.atomic
def create_contact(request, data: ContactInput):
    require_permission(request, 'supervision_view')
    require_permission(request, 'supervision_manage')
    _person(request, data.rbt_id, ['staff'])
    _person(request, data.supervisor_id, ['supervisor', 'admin'])
    if request.user.role == 'supervisor' and data.supervisor_id != request.user.pk:
        raise HttpError(403, 'Supervisors may only log their own supervision')
    if data.rbt_id == data.supervisor_id:
        raise HttpError(400, 'Self-supervision is not allowed')
    if data.date > timezone.localdate():
        raise HttpError(400, 'Supervision cannot be logged in the future')
    if not data.face_to_face:
        raise HttpError(400, 'Qualifying supervision requires real-time face-to-face contact')
    if not data.qualifications_confirmed:
        raise HttpError(400, 'Confirm supervisor eligibility and BACB portal linkage')
    row = SupervisionContact.objects.create(**data.dict(), created_by=request.user)
    return 201, _contact_output(row)


@router.post('/contacts/{contact_id}/void', response=ContactOutput)
@require_roles('admin', 'supervisor')
@transaction.atomic
def void_contact(request, contact_id: int, data: VoidInput):
    require_permission(request, 'supervision_view')
    require_permission(request, 'supervision_manage')
    row = _contacts(request).select_for_update().filter(pk=contact_id).first()
    if row is None:
        raise HttpError(404, 'Supervision contact not found')
    if request.user.role == 'supervisor' and row.supervisor_id != request.user.pk:
        raise HttpError(403, 'Only the supervisor or an administrator may void this contact')
    if row.void_reason:
        raise HttpError(409, 'Contact already voided')
    if not data.reason.strip():
        raise HttpError(400, 'A correction reason is required')
    row.void_reason = data.reason.strip()
    row.save(update_fields=['void_reason', 'updated_at'])
    return _contact_output(row)


@router.get('/contacts', response=list[ContactOutput])
@require_roles('admin', 'supervisor', 'staff')
def contacts(request, month: date, rbt_id: int | None = None):
    require_permission(request, 'supervision_view')
    end = _month(month)
    qs = _contacts(request).filter(date__range=(month, end))
    if rbt_id is not None:
        qs = qs.filter(rbt_id=rbt_id)
    return [_contact_output(row) for row in qs]


@router.put('/service-hours', response=ServiceHoursInput)
@require_roles('admin', 'supervisor')
@transaction.atomic
def service_hours(request, data: ServiceHoursInput):
    require_permission(request, 'supervision_view')
    require_permission(request, 'supervision_manage')
    _person(request, data.rbt_id, ['staff'])
    end = _month(data.month)
    if data.month > timezone.localdate().replace(day=1):
        raise HttpError(400, 'Service hours cannot be logged for a future month')
    if data.hours > Decimal(end.day * 24):
        raise HttpError(400, 'Hours exceed the calendar month')
    SupervisionServiceHours.objects.update_or_create(
        rbt_id=data.rbt_id, month=data.month,
        defaults={'hours': data.hours},
        create_defaults={'hours': data.hours, 'created_by': request.user},
    )
    return data


def _reports(request, month):
    end = _month(month)
    rbts = list(_rbts(request))
    hours = {row.rbt_id: row.hours for row in SupervisionServiceHours.objects.filter(
        month=month, rbt_id__in=[rbt.pk for rbt in rbts],
    )}
    by_rbt = {}
    for row in _contacts(request).filter(date__range=(month, end), void_reason='', qualifications_confirmed=True, face_to_face=True):
        by_rbt.setdefault(row.rbt_id, []).append(row)
    reports = []
    for rbt in rbts:
        rows = by_rbt.get(rbt.pk, [])
        total = sum((row.hours for row in rows), Decimal('0'))
        service = hours.get(rbt.pk)
        required = service * Decimal('0.05') if service is not None else None
        individual = sum(row.individual for row in rows)
        observed = sum(row.observed_services for row in rows)
        reports.append(dict(
            rbt_id=rbt.pk, rbt_name=rbt.full_name, month=month, service_hours=service,
            supervision_hours=total,
            percentage=(total / service * 100).quantize(Decimal('0.01')) if service else None,
            required_hours=required,
            remaining_hours=max(required - total, Decimal('0')) if required is not None else None,
            contacts=len(rows), individual_contacts=individual, observation_contacts=observed,
            recorded_requirements_met=bool(service and total >= required and len(rows) >= 2 and individual and observed),
        ))
    return reports


@router.get('/report', response=list[MonthlyReport])
@require_roles('admin', 'supervisor', 'staff')
def report(request, month: date):
    require_permission(request, 'supervision_view')
    return _reports(request, month)


@router.get('/report.csv')
@require_roles('admin', 'supervisor', 'staff')
def report_csv(request, month: date):
    require_permission(request, 'supervision_view')
    rows = _reports(request, month)
    fields = list(MonthlyReport.model_fields)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        # Prevent spreadsheet formula execution in names.
        row['rbt_name'] = "'" + row['rbt_name'] if row['rbt_name'].lstrip().startswith(('=', '+', '-', '@')) else row['rbt_name']
        writer.writerow(row)
    response = HttpResponse(output.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="supervision-{month:%Y-%m}.csv"'
    response['Cache-Control'] = 'no-store'
    return response
