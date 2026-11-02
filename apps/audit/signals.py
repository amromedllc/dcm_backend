import logging
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from apps.audit.middleware import get_current_request

logger = logging.getLogger(__name__)

# Models to audit — add any new tenant-scoped model here
_TRACKED = [
    'apps.sessions.models.SessionRun',
    'apps.sessions.models.SupervisionContact',
    'apps.sessions.models.SupervisionServiceHours',
    'apps.notes.models.LessonNote',
    'apps.clients.models.Client',
    'apps.programs.models.Program',
    'apps.programs.models.Target',
    'apps.notifications.models.Notification',
]

_pre_save_snapshots: dict[int, dict] = {}


def _model_label(instance) -> str:
    return type(instance).__name__


def _serialize(instance) -> dict:
    from django.forms.models import model_to_dict
    try:
        data = model_to_dict(instance)
        return {k: str(v) for k, v in data.items()}
    except Exception:
        return {}


def _request_meta():
    request = get_current_request()
    if not request:
        return None, None, None, None, None
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return None, None, None, None, None
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
    ip = ip.split(',')[0].strip() or None
    request_id = getattr(request, 'request_id', '')
    return user.id, user.email, user.role, ip, request_id


def _write_log(action, instance, changes=None):
    from apps.audit.models import AuditLog
    from shared.tenancy import current_org_id_or_none

    actor_id, actor_email, actor_role, ip, request_id = _request_meta()
    if not actor_id:
        return  # skip system/management-command writes

    org_id = current_org_id_or_none()
    if not org_id:
        return

    try:
        AuditLog.objects.create(
            organization_id=org_id,
            actor_id=actor_id,
            actor_email=actor_email,
            actor_role=actor_role,
            action=action,
            model=_model_label(instance),
            object_id=str(instance.pk),
            object_repr=str(instance)[:200],
            changes=changes or {},
            ip_address=ip,
            request_id=request_id or '',
        )
    except Exception:
        logger.exception('Failed to write audit log for %s %s', action, _model_label(instance))


def _connect_signals():
    import importlib
    for path in _TRACKED:
        module_path, class_name = path.rsplit('.', 1)
        try:
            module = importlib.import_module(module_path)
            model = getattr(module, class_name)
        except Exception:
            logger.warning('audit: could not import %s', path)
            continue

        @receiver(pre_save, sender=model, weak=False)
        def on_pre_save(sender, instance, **kwargs):
            if instance.pk:
                try:
                    old = sender.objects.get(pk=instance.pk)
                    _pre_save_snapshots[id(instance)] = _serialize(old)
                except sender.DoesNotExist:
                    pass

        @receiver(post_save, sender=model, weak=False)
        def on_post_save(sender, instance, created, **kwargs):
            if created:
                _write_log('create', instance)
            else:
                old = _pre_save_snapshots.pop(id(instance), {})
                new = _serialize(instance)
                changes = {
                    k: {'from': old.get(k), 'to': v}
                    for k, v in new.items()
                    if old.get(k) != v
                }
                if changes:
                    _write_log('update', instance, changes)

        @receiver(post_delete, sender=model, weak=False)
        def on_post_delete(sender, instance, **kwargs):
            _write_log('delete', instance)


_connect_signals()
