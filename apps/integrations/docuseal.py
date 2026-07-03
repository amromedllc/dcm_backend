"""
SSO handoff into DocuSeal (docuseal/), which already exposes a
`GET /sso/login?token=<jwt>` endpoint built for a TherapyPMS-driven login.
This mints a token that endpoint will accept for a DCM user, scoping them to
a DocuSeal Account per facility/organization so PDF e-sign templates stay
correctly siloed per practice.
"""
import jwt
from datetime import timedelta
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User

SSO_TOKEN_TTL_MINUTES = 5



def _facility_for_user(user: User) -> tuple[str, str | None]:
    if user.external_admin_id is not None:
        facility_id = str(user.external_admin_id)
        facility_name = None
        try:
            from apps.legacy.models import TpmsAdmin
            admin = TpmsAdmin.objects.using('therapypms').filter(id=user.external_admin_id).first()
            if admin:
                facility_name = admin.name or admin.first_name
        except Exception:
            pass
        return facility_id, facility_name

    if user.organization_id is not None:
        return f'dcm-org-{user.organization_id}', user.organization.name

    return f'dcm-user-{user.id}', None


def build_sso_redirect_url(user: User) -> str:
    facility_id, facility_name = _facility_for_user(user)
    now = timezone.now()
    payload = {
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'user_type': user.role,
        'facility_id': facility_id,
        'facility_name': facility_name,
        'iat': now.timestamp(),
        'exp': (now + timedelta(minutes=SSO_TOKEN_TTL_MINUTES)).timestamp(),
    }
    token = jwt.encode(payload, settings.DOCUSEAL_SSO_SECRET, algorithm='HS256')
    return f'{settings.DOCUSEAL_BASE_URL}/sso/login?token={token}'
