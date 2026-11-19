from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from shared.models import OrganizationScopedMixin
from shared.tenancy import TenantManager, TenantQuerySet, current_org_id
from .practice_planner.contracts import Category, Content
from .practice_planner.snapshots import NORMALIZATION_VERSION, content_digest, normalized_content


class SnapshotQuerySet(TenantQuerySet):
    def update(self, **kwargs):
        raise ValidationError('Content snapshots are append-only')

    def delete(self):
        raise ValidationError('Content snapshots cannot be deleted')

    def bulk_create(self, *args, **kwargs):
        raise ValidationError('Use retain_selection for content snapshots')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Content snapshots are append-only')


class PlannerContentSnapshot(OrganizationScopedMixin):
    organization = models.ForeignKey('tenants.Organization', on_delete=models.PROTECT)
    provider = models.CharField(max_length=80)
    planner_id = models.CharField(max_length=200)
    component_id = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=[(c.value, c.name) for c in Category])
    text = models.TextField()
    edition = models.CharField(max_length=100)
    version = models.CharField(max_length=100)
    language = models.CharField(max_length=20)
    attribution = models.TextField()
    normalized_payload = models.JSONField()
    normalization_version = models.PositiveIntegerField(default=NORMALIZATION_VERSION)
    digest = models.CharField(max_length=64)
    retrieved_at = models.DateTimeField(default=timezone.now, editable=False)
    objects = TenantManager.from_queryset(SnapshotQuerySet)()
    all_organizations = models.Manager.from_queryset(SnapshotQuerySet)()

    class Meta:
        base_manager_name = 'objects'
        constraints = [models.UniqueConstraint(fields=['organization', 'digest'], name='planner_snapshot_org_digest')]

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk is not None:
            raise ValidationError('Content snapshots are append-only')
        if self.organization_id is None:
            self.organization_id = current_org_id()
        if self.organization_id != current_org_id():
            raise ValidationError('Snapshot organization does not match tenant context')
        try:
            values = dict(self.normalized_payload)
            values['category'] = Category(values['category'])
            values['parent_ids'] = tuple(values['parent_ids'])
            content = Content(**values)
        except (TypeError, ValueError, KeyError):
            raise ValidationError('Invalid normalized content') from None
        if normalized_content(content) != self.normalized_payload:
            raise ValidationError('Noncanonical normalized content')
        for name in ('provider', 'planner_id', 'component_id', 'category', 'text', 'edition', 'version', 'language', 'attribution'):
            if getattr(self, name) != getattr(content, name):
                raise ValidationError('Snapshot fields do not match normalized content')
        if self.normalization_version != NORMALIZATION_VERSION or self.digest != content_digest(self.normalized_payload):
            raise ValidationError('Invalid snapshot digest or normalization version')
        self.full_clean(validate_constraints=False)
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Content snapshots cannot be deleted')

    def __str__(self):
        return f'PlannerContentSnapshot #{self.pk}'
