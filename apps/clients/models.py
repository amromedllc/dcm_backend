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


class TreatmentPlan(TenantAwareModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        FINALIZED = 'finalized', 'Finalized'
        ARCHIVED = 'archived', 'Archived'

    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='treatment_plans',
    )
    title = models.CharField(max_length=220)
    plan_date = models.DateField(db_index=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    sections = models.JSONField(default=dict)
    source_snapshot = models.JSONField(default=dict, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='finalized_treatment_plans',
        db_constraint=False,
    )

    _org_scoped_fk_fields = ('client',)

    class Meta:
        app_label = 'clients'
        ordering = ['-plan_date', '-created_at']

    def _derive_organization_id(self) -> int | None:
        return self.client.organization_id

    @property
    def client_id_value(self):
        return self.client_id

    def __str__(self) -> str:
        return f'{self.title} — {self.client}'
