from django.core.exceptions import PermissionDenied
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from .contracts import PlannerUnavailable
from .service import service_for_user

router = Router(auth=jwt_auth)


@router.get('/capabilities')
def capabilities(request):
    try:
        service_for_user(request)
    except PermissionDenied:
        raise HttpError(403, 'Practice planner access is not enabled') from None
    except PlannerUnavailable:
        raise HttpError(503, 'Practice planner provider unavailable') from None
    from .access import provider_status
    return {**provider_status(), 'enabled': True, 'content_lookup': True,
            'plan_builder': True, 'clinical_use': False}
