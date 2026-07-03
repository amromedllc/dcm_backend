from django_tenants.models import TenantMixin, DomainMixin
from django.db import models


class Organization(TenantMixin):
    class Plan(models.TextChoices):
        STARTER = 'starter', 'Starter'
        PROFESSIONAL = 'professional', 'Professional'
        ENTERPRISE = 'enterprise', 'Enterprise'

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STARTER)
    is_active = models.BooleanField(default=True)
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
