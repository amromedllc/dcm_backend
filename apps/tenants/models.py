from django_tenants.models import TenantMixin, DomainMixin
from django.db import models


class Organization(TenantMixin):
    class Plan(models.TextChoices):
        STARTER = 'starter', 'Starter'
        PROFESSIONAL = 'professional', 'Professional'
        ENTERPRISE = 'enterprise', 'Enterprise'

    class AutomaticLogoutMinutes(models.IntegerChoices):
        NINE_HOURS = 9 * 60, '9 hours'
        TWENTY_FOUR_HOURS = 24 * 60, '24 hours'
        ONE_WEEK = 7 * 24 * 60, '1 week'

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STARTER)
    is_active = models.BooleanField(default=True)
    automatic_logout_enabled = models.BooleanField(default=False)
    automatic_logout_minutes = models.PositiveIntegerField(
        choices=AutomaticLogoutMinutes.choices,
        default=AutomaticLogoutMinutes.NINE_HOURS,
    )
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
    class Meta:
        app_label = 'tenants'


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
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='tpms_admin_ids',
    )
    admin_id = models.IntegerField(unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'tenants'
        ordering = ['admin_id']

    def __str__(self):
        return f'{self.admin_id} → {self.organization.name}'

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
