import secrets
import hashlib
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', User.Role.ADMIN)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        SUPERVISOR = 'supervisor', 'Clinical Supervisor'
        STAFF = 'staff', 'RBT / Staff'
        CAREGIVER = 'caregiver', 'Caregiver'
        REPORTING = 'reporting', 'Reporting / Audit'

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # Set when the user authenticates via a linked external PM system — scopes their client/data access
    external_admin_id = models.IntegerField(null=True, blank=True, db_index=True)
    # External system employee pk — set at login for staff/supervisor, null for admin-only logins
    external_employee_id = models.IntegerField(null=True, blank=True, db_index=True)
    external_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    # Set for native users and TPMS-linked users after login. Privileges
    # (RolePermission) are keyed by this Organization. JWT is still bound to
    # the tenant resolved at login (see accounts.auth.token_tenant_mismatch).
    organization = models.ForeignKey(
        'tenants.Organization',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='users',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    class Meta:
        app_label = 'accounts'
        constraints = [
            models.CheckConstraint(
                name='caregiver_has_client_link',
                check=(
                    models.Q(role='caregiver', external_client_id__isnull=False)
                    | (~models.Q(role='caregiver') & models.Q(external_client_id__isnull=True))
                ),
            ),
        ]

    @property
    def full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def is_caregiver(self) -> bool:
        return self.role == self.Role.CAREGIVER and self.external_client_id is not None

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def __str__(self) -> str:
        return self.email


class APIKey(models.Model):
    """
    Organization-scoped API keys for partner / facility integrations.

    Each key is bound to exactly one Organization and backed by a dedicated
    service ``User`` (role ``admin``), so every ``request.user``-based
    scoping path in the API works unchanged for a keyed request. ``can_write``
    is off by default — a key can only issue GET/HEAD/OPTIONS requests until
    it is explicitly created with write access (see
    ``apps.accounts.auth.APIKeyAuth``). ``external_admin_id`` is copied from
    the creating user so keys minted by a TPMS-linked admin stay scoped to
    that admin's practice (one Organization can front several TPMS practices).

    The raw key is shown once at creation — only its SHA-256 hash is stored.
    """
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=8, db_index=True)
    key_hash = models.CharField(max_length=64)
    organization = models.ForeignKey(
        'tenants.Organization',
        on_delete=models.CASCADE,
        null=True,
        related_name='api_keys',
    )
    external_admin_id = models.IntegerField(null=True, blank=True, db_index=True)
    service_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        related_name='+',
    )
    can_write = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_keys',
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'accounts'

    @classmethod
    def generate(
        cls,
        name: str,
        created_by: User,
        *,
        organization_id: int,
        external_admin_id: int | None = None,
        can_write: bool = False,
        expires_at=None,
    ) -> tuple['APIKey', str]:
        raw_key = f'dcm_{secrets.token_urlsafe(32)}'
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        service_user = User.objects.create(
            email=f'apikey.{raw_key[4:12]}@service.dcm.local',
            first_name='API Key',
            last_name=name[:100],
            role=User.Role.ADMIN,
            is_active=True,
            organization_id=organization_id,
            external_admin_id=external_admin_id,
        )
        service_user.set_unusable_password()
        service_user.save(update_fields=['password'])

        instance = cls.objects.create(
            name=name,
            key_prefix=raw_key[:8],
            key_hash=key_hash,
            organization_id=organization_id,
            external_admin_id=external_admin_id,
            service_user=service_user,
            can_write=can_write,
            created_by=created_by,
            expires_at=expires_at,
        )
        return instance, raw_key

    @classmethod
    def verify(cls, raw_key: str) -> 'APIKey | None':
        if not raw_key.startswith('dcm_'):
            return None
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        try:
            key = cls.objects.select_related('service_user').get(
                key_prefix=raw_key[:8],
                key_hash=key_hash,
                is_active=True,
            )
        except cls.DoesNotExist:
            return None

        if key.expires_at and key.expires_at < timezone.now():
            return None

        # Throttle the write: one row-write per key per minute at most, not
        # one per request.
        now = timezone.now()
        if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 60:
            key.last_used_at = now
            key.save(update_fields=['last_used_at'])
        return key

    @classmethod
    def org_id_for(cls, raw_key: str) -> int | None:
        """Resolve the owning organization id without verify()'s side effects
        — used by TenantResolverMiddleware to bind the request tenant before
        Ninja auth runs."""
        if not raw_key.startswith('dcm_'):
            return None
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return (
            cls.objects.filter(
                key_prefix=raw_key[:8],
                key_hash=key_hash,
                is_active=True,
            )
            .values_list('organization_id', flat=True)
            .first()
        )

    def __str__(self) -> str:
        return f'{self.name} ({self.key_prefix}...)'


class RolePermission(models.Model):
    """
    Facility-scoped permission matrix for a role.

    ``permissions`` is a JSON object like
    {"dashboard": true, "settings_tags_create": false, ...}.

    One row per (organization, role) pair — each Organization (facility /
    tenant) has its own matrix, loaded for the facility the user logged into.
    """
    organization = models.ForeignKey(
        'tenants.Organization',
        on_delete=models.CASCADE,
        related_name='role_permissions',
    )
    role = models.CharField(max_length=20, choices=User.Role.choices)
    permissions = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'accounts'
        unique_together = [('organization', 'role')]

    def __str__(self) -> str:
        return f'{self.organization} / {self.role}'
