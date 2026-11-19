"""Stage 5: progress-note autofill provenance, signed-note addenda, and homework.

Provenance and addenda rows are append-only (see the matching PostgreSQL
triggers) — a correction is a new row, never an edit to history.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from shared.models import OrganizationScopedMixin


class NoteContentApplication(OrganizationScopedMixin):
    """One autofill action: which plan/objectives/templates were used and what
    was actually written (or skipped, if existing narrative was preserved)."""
    note = models.ForeignKey('notes.LessonNote', on_delete=models.CASCADE, related_name='planner_applications')
    plan = models.ForeignKey('clients.TreatmentPlan', on_delete=models.PROTECT, related_name='+')
    objective_ids = models.JSONField(default=list)
    template_snapshot_ids = models.JSONField(default=list)
    field_key = models.CharField(max_length=100)
    rendered_content = models.TextField()
    applied = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', db_constraint=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    _org_scoped_fk_fields = ('note', 'plan')

    class Meta:
        app_label = 'notes'
        ordering = ['created_at', 'id']

    def _derive_organization_id(self) -> int | None:
        return self.note.organization_id

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Planner note applications are append-only')
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'Planner application for note {self.note_id} → {self.field_key}'


class NoteAddendum(OrganizationScopedMixin):
    """Append-only correction attached to an already-signed note."""
    note = models.ForeignKey('notes.LessonNote', on_delete=models.CASCADE, related_name='addenda')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', db_constraint=False,
    )
    author_name = models.CharField(max_length=200)
    author_role = models.CharField(max_length=50)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    _org_scoped_fk_fields = ('note',)

    class Meta:
        app_label = 'notes'
        ordering = ['created_at', 'id']

    def _derive_organization_id(self) -> int | None:
        return self.note.organization_id

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Note addenda are append-only')
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'Addendum on note {self.note_id} by {self.author_name}'


class ClientHomeworkAssignment(OrganizationScopedMixin):
    class Status(models.TextChoices):
        ASSIGNED = 'assigned', 'Assigned'
        COMPLETED = 'completed', 'Completed'

    client = models.ForeignKey('clients.Client', on_delete=models.CASCADE, related_name='homework_assignments')
    note = models.ForeignKey(
        'notes.LessonNote', on_delete=models.SET_NULL, null=True, blank=True, related_name='homework_assignments',
    )
    source = models.ForeignKey('integrations.PlannerContentSnapshot', on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', db_constraint=False,
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    _org_scoped_fk_fields = ('client', 'note', 'source')
    _immutable_fields = ('client_id', 'note_id', 'source_id')

    class Meta:
        app_label = 'notes'
        ordering = ['-assigned_at', '-id']

    def _derive_organization_id(self) -> int | None:
        return self.client.organization_id

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, field) != getattr(self, field) for field in self._immutable_fields):
                raise ValidationError('Homework assignment identity is immutable')
        if self.source.category != 'homework':
            raise ValidationError('Only homework content can be assigned as homework')
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'Homework for client {self.client_id} [{self.status}]'
