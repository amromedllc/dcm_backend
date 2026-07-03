from django.db import models
from django.conf import settings


class TenantAwareModel(models.Model):
    """
    Base for all tenant-scoped clinical data.
    Schema-based tenancy (django-tenants) enforces isolation at the DB level,
    so no organization_id field is needed — the schema IS the tenant boundary.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        db_constraint=False,  # User lives in public schema; tenant models can't have a real cross-schema FK constraint
    )

    class Meta:
        abstract = True
