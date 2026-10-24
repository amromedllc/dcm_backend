"""Security events with a fixed metadata schema: never serialize auth inputs."""
import ipaddress
import logging
from functools import wraps
from uuid import UUID

from django.db import transaction
from ninja.errors import HttpError

from shared.audit import log_event

logger = logging.getLogger(__name__)


def _id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def record_security_event(event, *, request=None, actor_id=None, organization_id=None,
                          outcome='success', status=200):
    actor_id = _id(actor_id)
    organization_id = _id(getattr(getattr(request, 'tenant', None), 'pk', None)) or _id(organization_id)
    meta = getattr(request, 'META', {})
    try:
        ip = str(ipaddress.ip_address(meta.get('REMOTE_ADDR', '')))
    except ValueError:
        ip = None
    try:
        request_id = str(UUID(str(getattr(request, 'request_id', ''))))
    except ValueError:
        request_id = ''
    details = {'event': event, 'outcome': outcome, 'status': status,
               'organization_id': organization_id, 'ip_address': ip, 'request_id': request_id}
    # No request body, query string, headers, exception text or credential values.
    try:
        log_event(event, actor_id, 'AccountSecurity', actor_id, details)
    except Exception:
        logger.error('Security audit structured sink unavailable')
    if organization_id is None:
        return  # No safe tenant destination; the structured event still exists.
    from apps.audit.models import AuditLog
    try:
        # A savepoint prevents an audit failure from poisoning an outer transaction.
        with transaction.atomic():
            AuditLog.objects.create(
                organization_id=organization_id, actor_id=actor_id or 0,
                actor_email='', actor_role='', action='update', model='AccountSecurity',
                object_id=str(actor_id or 0), object_repr=event, changes=details,
                ip_address=ip, request_id=request_id,
            )
    except Exception:
        # Do not include DB exceptions: they can contain rejected field values.
        logger.error('Security audit database sink unavailable')


def audit_security(event):
    """Record the final endpoint result, after authentication checks finish."""
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            def emit(outcome, status, actor=None):
                actor = _id(actor) or _id(getattr(request, '_security_actor_id', None))
                if actor is None:
                    actor = _id(getattr(getattr(request, 'user', None), 'pk', None))
                record_security_event(event, request=request, actor_id=actor,
                                      outcome=outcome, status=status)
            try:
                result = view(request, *args, **kwargs)
            except HttpError as exc:
                emit('rejected', exc.status_code)
                raise
            except Exception:
                emit('error', 500)
                raise
            if event == 'auth.sso':
                emit(getattr(request, '_security_sso_outcome', 'rejected'), 302)
            else:
                outcome = 'challenge' if getattr(result, 'mfa_required', False) else 'success'
                status = result[0] if isinstance(result, tuple) else 200
                emit(outcome, status, getattr(result, 'user_id', None))
            return result
        return wrapped
    return decorate
