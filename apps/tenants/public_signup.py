"""
Public, unauthenticated self-service demo signup for the marketing website.

Not part of the normal superadmin-provisioning flow (see docs/subscription-billing.md:
"this project provisions organizations centrally; it has no public
self-registration endpoint" — this is the one deliberate exception). Access is
gated by a platform-wide secret (PublicSignupKey, header X-Signup-Key) handed
to one trusted caller, plus a Cloudflare Turnstile captcha token and the
existing per-IP rate limit on this path (see shared/middleware.py).

Creates: an Organization on the Free Trial plan (30-day trial_ends_at), its
primary Domain, an admin User from the submitted details, and a small demo
dataset (apps.tenants.demo_seed) so the new admin has something to look at
immediately. Returns a signed-in token pair so the marketing site can hand the
visitor straight into the app.
"""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.api import _issue_tokens
from apps.accounts.models import User
from shared.tenancy import tenant_context
from .models import Domain, Organization, PublicSignupKey
from .schemas import PublicSignupRequest, PublicSignupResponse
from .captcha import verify_captcha
from .api import _domain_for_slug, _normalize_slug, _schema_from_slug
from .services import copy_default_target_statuses_to_org
from .demo_seed import seed_demo_dataset

logger = logging.getLogger(__name__)

router = Router()

_PASSWORD_MIN_LENGTH = 8
_PHONE_RE = re.compile(r'^\+?[0-9()\-.\s]{7,20}$')


def _client_ip(request) -> str:
    return request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[0].strip()


def _require_signup_key(request) -> None:
    raw_key = request.headers.get('X-Signup-Key', '')
    if not raw_key or PublicSignupKey.verify(raw_key) is None:
        raise HttpError(401, 'Invalid or missing signup key')


def _unique_slug_and_schema(organization_name: str) -> tuple[str, str]:
    base_slug = _normalize_slug(organization_name)
    slug = base_slug
    schema_name = _schema_from_slug(slug)
    suffix = 1
    while Organization.objects.filter(slug=slug).exists() or Organization.objects.filter(schema_name=schema_name).exists():
        suffix += 1
        slug = f'{base_slug}-{suffix}'
        schema_name = _schema_from_slug(slug)
    return slug, schema_name


@router.post('/signup', response={201: PublicSignupResponse}, auth=None)
def public_signup(request, data: PublicSignupRequest):
    _require_signup_key(request)

    # Off until a real Turnstile site is configured on the marketing side —
    # set REQUIRE_SIGNUP_CAPTCHA=true once it is. X-Signup-Key plus the
    # existing per-IP rate limit on this path are the abuse guard until then.
    if getattr(settings, 'REQUIRE_SIGNUP_CAPTCHA', False):
        if not verify_captcha(data.captcha_token, _client_ip(request)):
            raise HttpError(400, 'Captcha verification failed')

    first_name = data.first_name.strip()
    last_name = data.last_name.strip()
    email = data.email.strip().lower()
    organization_name = data.organization_name.strip()
    phone_number = data.phone_number.strip()

    if not first_name or not last_name:
        raise HttpError(400, 'First and last name are required')
    if not organization_name:
        raise HttpError(400, 'Organization name is required')
    try:
        validate_email(email)
    except ValidationError:
        raise HttpError(400, 'A valid email address is required')
    if not _PHONE_RE.match(phone_number):
        raise HttpError(400, 'A valid phone number is required')
    if data.password != data.confirm_password:
        raise HttpError(400, 'Passwords do not match')
    if len(data.password) < _PASSWORD_MIN_LENGTH:
        raise HttpError(400, f'Password must be at least {_PASSWORD_MIN_LENGTH} characters')
    if User.objects.filter(email__iexact=email).exists():
        raise HttpError(409, 'A user with this email already exists')

    slug, schema_name = _unique_slug_and_schema(organization_name)
    domain = _domain_for_slug(slug)
    trial_ends_at = timezone.now() + timedelta(days=Organization.FREE_TRIAL_DAYS)

    with transaction.atomic(), schema_context(get_public_schema_name()):
        org = Organization.objects.create(
            name=organization_name,
            slug=slug,
            schema_name=schema_name,
            plan=Organization.Plan.FREE_TRIAL,
            is_active=True,
            trial_ends_at=trial_ends_at,
        )
        Domain.objects.create(tenant=org, domain=domain, is_primary=True)

        admin_user = User.objects.create_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            role=User.Role.ADMIN,
            password=data.password,
            organization=org,
        )

        with schema_context(schema_name), tenant_context(org.pk):
            copy_default_target_statuses_to_org(org.id)
            seed_demo_dataset(org, admin_user)

    tokens = _issue_tokens(admin_user, org.pk)

    logger.info('Public demo signup: organization %s (%s), admin %s', org.id, org.slug, admin_user.email)

    return 201, {
        'organization_id': org.id,
        'organization_name': org.name,
        'organization_slug': org.slug,
        'domain': domain,
        'trial_ends_at': trial_ends_at,
        'user_id': admin_user.id,
        'email': admin_user.email,
        'access_token': tokens.access_token,
        'refresh_token': tokens.refresh_token,
    }
