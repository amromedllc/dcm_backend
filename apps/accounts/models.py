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
    # Set for native (non-linked) users. Null for externally-linked (TPMS)
    # users, who are additionally scoped via external_admin_id — but every
    # user's JWT is still bound to the tenant resolved at login time
    # regardless of this field (see accounts.auth.token_tenant_mismatch).
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

    @property
    def full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'.strip()

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def __str__(self) -> str:
        return self.email


class APIKey(models.Model):
    """
    Tenant-scoped API keys for facility-to-facility integrations.
    The raw key is shown once at creation — only the SHA-256 hash is stored.
    """
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=8, db_index=True)
    key_hash = models.CharField(max_length=64)
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
        expires_at=None,
    ) -> tuple['APIKey', str]:
        raw_key = f'dcm_{secrets.token_urlsafe(32)}'
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        instance = cls.objects.create(
            name=name,
            key_prefix=raw_key[:8],
            key_hash=key_hash,
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
            key = cls.objects.select_related('created_by').get(
                key_prefix=raw_key[:8],
                key_hash=key_hash,
                is_active=True,
            )
            if key.expires_at and key.expires_at < timezone.now():
                return None
            key.last_used_at = timezone.now()
            key.save(update_fields=['last_used_at'])
            return key
        except cls.DoesNotExist:
            return None

    def __str__(self) -> str:
        return f'{self.name} ({self.key_prefix}...)'


class RolePermission(models.Model):
    """
    Stores which permission keys are granted to a role, per organisation.
    `permissions` is a JSON object like {"dashboard": true, "review_queue": false, ...}
    One row per (organization, role) pair.
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
