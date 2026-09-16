from django.conf import settings
from django.db import models
from shared.models import OrganizationScopedMixin, TenantAwareModel


class Client(TenantAwareModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'
        DISCHARGED = 'discharged', 'Discharged'
        ON_HOLD = 'on_hold', 'On Hold'

    external_id = models.CharField(max_length=100, blank=True, db_index=True)
    # admin_id in the linked external PM system this client belongs to — used to scope access per login
    external_admin_id = models.IntegerField(null=True, blank=True, db_index=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    intake_date = models.DateField(null=True, blank=True)
    discharge_date = models.DateField(null=True, blank=True)
    internal_notes = models.TextField(blank=True)

    class Meta:
        app_label = 'clients'
        ordering = ['last_name', 'first_name']

    @property
    def full_name(self) -> str:
        display = self.preferred_name or self.first_name
        return f'{display} {self.last_name}'.strip()

    def __str__(self) -> str:
        return self.full_name


class ClientStaffAssignment(OrganizationScopedMixin):
    """Tracks which staff members are assigned to which clients."""
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='staff_assignments',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='client_assignments',
        db_constraint=False,
    )
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    def _derive_organization_id(self) -> int | None:
        return self.client.organization_id

    class Meta:
        app_label = 'clients'
        unique_together = [['client', 'user']]

    def __str__(self) -> str:
        return f'{self.user_id} → {self.client}'
