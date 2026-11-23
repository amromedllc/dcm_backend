import hashlib
import secrets

from django_tenants.models import TenantMixin, DomainMixin
from django.conf import settings
from django.db import models
from django.utils import timezone


class Organization(TenantMixin):
    class Plan(models.TextChoices):
        FREE_TRIAL = 'free_trial', 'Free Trial (30 days)'
        STARTER = 'starter', 'Starter'
        PROFESSIONAL = 'professional', 'Professional'
        ENTERPRISE = 'enterprise', 'Enterprise'

    FREE_TRIAL_DAYS = 30

    class AutomaticLogoutMinutes(models.IntegerChoices):
        FIVE_MINUTES = 5, '5 minutes'
        TEN_MINUTES = 10, '10 minutes'
        FIFTEEN_MINUTES = 15, '15 minutes'
        THIRTY_MINUTES = 30, '30 minutes'

    class IntegrationPlatform(models.TextChoices):
        THERAPY_PMS = 'therapy_pms', 'TherapyPMS'

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STARTER)
    is_active = models.BooleanField(default=True)
    automatic_logout_enabled = models.BooleanField(default=False)
    automatic_logout_minutes = models.PositiveIntegerField(
        choices=AutomaticLogoutMinutes.choices,
        default=AutomaticLogoutMinutes.FIFTEEN_MINUTES,
    )
    pin_lock_allowed = models.BooleanField(default=False)
    integration_platform = models.CharField(
        max_length=30, choices=IntegrationPlatform.choices, blank=True, default='',
    )
    integration_email = models.EmailField(blank=True, default='')
    integration_password_encrypted = models.TextField(blank=True, default='')
    integration_admin_id = models.IntegerField(null=True, blank=True, db_index=True)
    # Matches a stray `ai_enabled NOT NULL` column already present on this
    # table in at least one environment; not otherwise wired up at the
    # Organization level today (see OrganizationTpmsAdminId.ai_enabled for
    # the per-facility flag actually used by AI drafting).
    ai_enabled = models.BooleanField(default=False)
    # Set on creation when plan == FREE_TRIAL; see tasks.deactivate_expired_free_trials.
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # django-tenants requires this — schema is auto-created on save
    auto_create_schema = True

    class Meta:
        app_label = 'tenants'

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    """
    Maps a hostname (e.g. acme.dcm-platform.com) to an Organization tenant.
    Each Organization must have at least one Domain.
    """
    # django-tenants' DomainMixin declares this field globally unique. Shared
    # Progressly hostnames need one row per organization instead.
    domain = models.CharField(max_length=253, db_index=True)

    class Meta:
        app_label = 'tenants'
        constraints = [
            models.UniqueConstraint(fields=['domain', 'tenant'], name='unique_domain_per_tenant'),
        ]


class OrganizationTpmsAdminId(models.Model):
    """
    A TherapyPMS practice (admin_id) that should route into this
    Organization's schema at login — see accounts.api._tpms_auth.

    One Organization can front several TPMS practices (multiple admin_ids
    pointing at the same schema), but each admin_id maps to exactly one
    Organization (admin_id stays globally unique) so tenant binding at login
    stays unambiguous — a given TPMS practice can never resolve to two
    different DCM organizations.
    """
    class FormBuilderMode(models.TextChoices):
        NEW = 'new', 'New Form Builder only'
        OLD = 'old', 'Old Form Builder only'
        BOTH = 'both', 'Both form builders'

    form_builder_mode = models.CharField(
        max_length=4, choices=FormBuilderMode.choices, default=FormBuilderMode.NEW,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='tpms_admin_ids',
    )
    admin_id = models.IntegerField(unique=True, db_index=True)
    facility_name = models.CharField(max_length=200, blank=True)
    email_notifications_enabled = models.BooleanField(default=False)
    ai_enabled = models.BooleanField(default=False)
    integration_platform = models.CharField(max_length=30, blank=True, default='')
    integration_email = models.EmailField(blank=True, default='')
    integration_password_encrypted = models.TextField(blank=True, default='')
    integration_admin_id = models.IntegerField(null=True, blank=True)
    last_appointment_sync_at = models.DateTimeField(null=True, blank=True)
    last_client_sync_at = models.DateTimeField(null=True, blank=True)
    last_provider_sync_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'tenants'
        ordering = ['admin_id']

    def __str__(self):
        label = f'{self.admin_id} ({self.facility_name})' if self.facility_name else str(self.admin_id)
        return f'{label} → {self.organization.name}'

class DefaultTargetStatus(models.Model):
    key = models.SlugField(max_length=20, unique=True)
    label = models.CharField(max_length=50)
    color = models.CharField(max_length=7, default='#6366f1')  # hex
    icon = models.CharField(max_length=30, default='circle')
    is_staff_visible = models.BooleanField(default=False, help_text='Shown to staff in the session recording view')
    is_default = models.BooleanField(default=False, help_text='Starting status for newly created targets')
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'tenants'
        ordering = ['display_order', 'label']

    def __str__(self):
        return self.label


class PublicSignupKey(models.Model):
    """
    A platform-wide secret (not bound to any Organization) that authorizes
    the public, unauthenticated self-service demo-signup endpoint
    (see apps.tenants.public_signup). Meant to be handed to one trusted
    caller — e.g. the marketing website's backend — not shipped to a browser.

    The raw key is shown once at creation — only its SHA-256 hash is stored,
    same convention as apps.accounts.models.APIKey.
    """
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=12, db_index=True)
    key_hash = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'tenants'

    @classmethod
    def generate(cls, name: str, created_by=None) -> tuple['PublicSignupKey', str]:
        raw_key = f'signup_{secrets.token_urlsafe(32)}'
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        instance = cls.objects.create(
            name=name,
            key_prefix=raw_key[:12],
            key_hash=key_hash,
            created_by=created_by,
        )
        return instance, raw_key

    @classmethod
    def verify(cls, raw_key: str) -> 'PublicSignupKey | None':
        if not raw_key.startswith('signup_'):
            return None
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        try:
            key = cls.objects.get(key_prefix=raw_key[:12], key_hash=key_hash, is_active=True)
        except cls.DoesNotExist:
            return None
        key.last_used_at = timezone.now()
        key.save(update_fields=['last_used_at'])
        return key

    def __str__(self):
        return self.name
