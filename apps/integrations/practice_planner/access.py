"""Request-facing facility gate. Provider configuration alone grants no access."""
from django.conf import settings
from django.db import connection

from shared.tenancy import current_org_id_or_none


def provider_status():
    configured = getattr(settings, 'PRACTICE_PLANNER_PROVIDER', 'disabled')
    # Fixed values only: never echo arbitrary configuration, keys or vendor errors.
    if configured == 'mock':
        ready = bool(settings.DEBUG)
        return {'provider': 'synthetic', 'ready': ready, 'is_mock': True,
                'reason': 'ready' if ready else 'mock_not_allowed'}
    if configured == 'live':
        # Partial live adapter (Stage 6): problem/progress-note/homework content
        # only — goal/objective pairing is unresolved with Wiley, so the builder's
        # goal step will come up empty against live data. Readiness just means "a
        # key is configured"; superadmin per-facility enable + entitlement (below)
        # is the actual access control, matching "pilot with superadmin-enabled
        # facilities." Developer testing against QA only — confirm before any
        # production facility is ever entitled for live.
        ready = bool(getattr(settings, 'WILEY_API_KEY', ''))
        return {'provider': 'wiley', 'ready': ready, 'is_mock': False,
                'reason': 'ready' if ready else 'live_key_not_configured'}
    return {'provider': None, 'ready': False, 'is_mock': False, 'reason': 'provider_unavailable'}


def staff_identity(request):
    """Verified active staff/tenant identity, independent of feature availability.

    Historical clinical reads still use this boundary after feature revocation.
    """
    from apps.accounts.models import User
    from apps.tenants.models import Organization

    def denied(reason):
        return None, reason

    user = getattr(request, 'user', None)
    payload = getattr(request, '_jwt_payload', None)
    if (not isinstance(user, User) or not user.pk or getattr(request, 'api_key', None) is not None
            or not isinstance(payload, dict) or payload.get('type') != 'access'
            or str(payload.get('sub')) != str(user.pk)):
        return denied('staff_session_required')
    tenant = getattr(request, 'tenant', None)
    org_id = getattr(tenant, 'pk', None)
    try:
        token_org_id = int(payload.get('org_id'))
    except (ValueError, TypeError):
        return denied('organization_mismatch')
    if not org_id or org_id != token_org_id or current_org_id_or_none() != org_id:
        return denied('organization_mismatch')
    schema = getattr(connection, 'schema_name', None)
    if schema is not None and schema != getattr(tenant, 'schema_name', None):
        return denied('organization_mismatch')
    # Re-read mutable membership and activation flags on every operation.
    fresh = User.objects.filter(pk=user.pk, is_active=True).first()
    if fresh is None or fresh.role not in ('admin', 'supervisor', 'staff'):
        return denied('staff_role_required')
    if fresh.organization_id not in (None, org_id):
        return denied('organization_mismatch')
    if not Organization.objects.filter(pk=org_id, is_active=True).exists():
        return denied('organization_inactive')
    return fresh, 'ready'


def availability_for_request(request):
    from apps.tenants.models import OrganizationTpmsAdminId
    status = provider_status()

    def denied(reason):
        return {**status, 'enabled': False, 'reason': reason}

    fresh, reason = staff_identity(request)
    if fresh is None:
        return denied(reason)
    org_id = request.tenant.pk
    if fresh.external_admin_id is None:
        return denied('facility_not_mapped')
    facility = OrganizationTpmsAdminId.objects.filter(
        organization_id=org_id, admin_id=fresh.external_admin_id,
    ).first()
    if facility is None:
        return denied('facility_not_mapped')
    if not facility.wiley_enabled:
        return denied('facility_disabled')
    if not status['ready']:
        return denied(status['reason'])
    if not status['is_mock'] and not facility.wiley_entitled:
        return denied('license_required')
    return {**status, 'enabled': True}


def validate_enablement(enabled, entitled):
    """Only enabling requires readiness. Disable/revoke must work in an outage."""
    from ninja.errors import HttpError
    if not enabled:
        return
    status = provider_status()
    if not status['ready']:
        raise HttpError(409, 'Practice planner provider is not available')
    if not status['is_mock'] and not entitled:
        raise HttpError(409, 'Confirm the Wiley license before enabling access')


def audit_feature_change(actor_id, facility, previous):
    from django.db import transaction
    from shared.audit import log_event
    current = {'enabled': facility.wiley_enabled, 'entitled': facility.wiley_entitled}
    if previous == current:
        return
    org_id, mapping_id = facility.organization_id, facility.pk
    transaction.on_commit(lambda: log_event('wiley_feature_changed', actor_id, 'facility', mapping_id,
        {'organization_id': org_id, 'previous': previous, 'current': current}))
