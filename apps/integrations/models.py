from django.conf import settings
from django.db import models
from shared.models import TenantAwareModel


class Provider(TenantAwareModel):
    """A TherapyPMS provider synced in by Integrations → Pull Providers.

    Separate from the User accounts pull_providers() also creates/updates —
    this is just a directory record of what TherapyPMS returned, kept even
    if no login account exists for the person (or independently of whatever
    happens to that account afterwards). `user` links to the corresponding
    login account when one was created, but is not required.
    """
    external_employee_id = models.IntegerField(db_index=True)
    external_admin_id = models.IntegerField(null=True, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    first_name = models.CharField(max_length=100, blank=True)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    # Encrypted at rest (see integrations.tpms_credentials.encrypt_ssn/decrypt_ssn)
    # — never stored or logged in plain text.
    ssn_encrypted = models.TextField(blank=True, default='')
    external_other_id = models.CharField(max_length=100, blank=True)
    office_phone = models.CharField(max_length=30, blank=True)
    office_fax = models.CharField(max_length=30, blank=True)
    role = models.CharField(max_length=20, blank=True)
    employee_type = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='provider_profile',
        db_constraint=False,
    )
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'integrations'
        ordering = ['last_name', 'first_name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'external_admin_id', 'external_employee_id'],
                name='uniq_provider_per_org_practice_employee',
            ),
        ]

    @property
    def full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def ssn(self) -> str:
        """Decrypted SSN — computed on access, never cached on the instance."""
        from apps.integrations.tpms_credentials import decrypt_ssn
        return decrypt_ssn(self.ssn_encrypted)

    def __str__(self) -> str:
        return self.full_name or self.email or f'Provider #{self.external_employee_id}'
