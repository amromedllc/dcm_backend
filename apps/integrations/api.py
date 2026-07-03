from ninja import Router, Schema

from apps.accounts.auth import jwt_auth
from .docuseal import build_sso_redirect_url

router = Router(auth=jwt_auth)


class SsoUrlResponse(Schema):
    url: str


@router.get('/docuseal/sso-url', response=SsoUrlResponse)
def docuseal_sso_url(request):
    return {'url': build_sso_redirect_url(request.user)}
