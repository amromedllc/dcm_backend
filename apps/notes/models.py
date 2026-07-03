from django.conf import settings
from django.db import models
from shared.models import TenantAwareModel


class NoteTemplate(TenantAwareModel):
    """
    Defines the structure of a clinical note — field keys, labels, types, and
    required/optional constraints. The `body` JSONB on LessonNote maps these keys
    to their values at note creation time.

    Field schema per item in `fields`:
    {
      "key": "session_summary",
      "label": "Session Summary",
      "type": "textarea",          # text | textarea | number | boolean | select | multiselect | date
      "required": true,
      "placeholder": "",
      "options": []                # used for select / multiselect types
    }
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    fields = models.JSONField(default=list)
    is_org_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'notes'
        ordering = ['name']

    def required_field_keys(self) -> list[str]:
        return [f['key'] for f in self.fields if f.get('required')]

    def __str__(self) -> str:
        return self.name


class LessonNote(TenantAwareModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SUBMITTED = 'submitted', 'Submitted'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    session_run = models.OneToOneField(
        'dcm_sessions.SessionRun',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='note',
    )
    tpms_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='authored_notes',
        db_constraint=False,
    )
    template = models.ForeignKey(
        NoteTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notes',
    )
    note_date = models.DateField(db_index=True)
    body = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by_id = models.IntegerField(null=True, blank=True, db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by_id = models.IntegerField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    requires_caregiver_signature = models.BooleanField(default=False)

    class Meta:
        app_label = 'notes'
        ordering = ['-note_date', '-created_at']

    @property
    def is_editable(self) -> bool:
        return self.status in (self.Status.DRAFT, self.Status.REJECTED)

    @property
    def client_id(self):
        return self.tpms_client_id

    def __str__(self) -> str:
        return f'Note {self.id} [{self.status}] — {self.tpms_client_id} {self.note_date}'


class NoteAssignment(TenantAwareModel):
    """
    Admin/supervisor assigns a NoteTemplate to a TPMS appointment.
    Matches TPMS's docu_seal_templates / session_notes_avails pattern.
    Staff sees these assignments and fills each one; once filled the note FK is set.
    """
    tpms_appointment_id = models.BigIntegerField(db_index=True)
    tpms_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    template = models.ForeignKey(
        NoteTemplate, on_delete=models.CASCADE, related_name='assignments',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='note_assignments_given', db_constraint=False,
    )
    note = models.OneToOneField(
        LessonNote, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assignment',
    )

    class Meta:
        app_label = 'notes'
        unique_together = [('tpms_appointment_id', 'template')]
        ordering = ['created_at']

    @property
    def is_filled(self):
        return self.note_id is not None

    def __str__(self):
        return f'Assignment {self.id} — appt {self.tpms_appointment_id} → {self.template.name}'


class NoteSignature(models.Model):
    """
    Timestamped, attributable signature on an approved note.
    signer_name is stored as a snapshot so the audit trail survives
    if the user account is later deactivated or renamed.
    """
    class SignatureType(models.TextChoices):
        STAFF = 'staff', 'Staff'
        SUPERVISOR = 'supervisor', 'Supervisor'
        CAREGIVER = 'caregiver', 'Caregiver'

    note = models.ForeignKey(
        LessonNote,
        on_delete=models.CASCADE,
        related_name='signatures',
    )
    signer_id = models.IntegerField(db_index=True)
    signer_name = models.CharField(max_length=200)
    signer_role = models.CharField(max_length=50)
    signature_type = models.CharField(max_length=20, choices=SignatureType.choices)
    signature_data = models.TextField(blank=True)
    signed_at = models.DateTimeField(auto_now_add=True)
    ip_address_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        app_label = 'notes'
        ordering = ['signed_at']

    def __str__(self) -> str:
        return f'{self.signer_name} [{self.signature_type}] @ {self.signed_at:%Y-%m-%d %H:%M}'
