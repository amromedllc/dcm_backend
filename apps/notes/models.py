from django.conf import settings
from django.db import models
from shared.models import OrganizationScopedMixin, TenantAwareModel


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

    TEMPLATE_TYPES = [
        ('notes', 'Notes'),
        ('forms', 'Forms'),
        ('reports', 'Reports'),
    ]
    template_type = models.CharField(max_length=20, choices=TEMPLATE_TYPES, default='notes')
    body_template = models.TextField(blank=True, default='')
    require_completer_signature = models.BooleanField(default=False)
    require_additional_signatures = models.BooleanField(default=False)
    lock_content = models.BooleanField(default=False)

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
    external_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
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

    # Set when this note is filled out as a DocuSeal e-sign template rather
    # than (or in addition to) the JSON `body` above. docuseal_template_id
    # being set is what makes completing it a prerequisite for submitting the
    # session — see apps.sessions.services.submit_session.
    docuseal_template_id = models.IntegerField(null=True, blank=True)
    docuseal_submitter_id = models.IntegerField(null=True, blank=True, db_index=True)
    docuseal_slug = models.CharField(max_length=64, blank=True)
    docuseal_completed_at = models.DateTimeField(null=True, blank=True)

    # session_run is a cross-app FK (-> dcm_sessions.SessionRun) and nullable,
    # so organization comes from the ambient context, not derived from it.
    _org_scoped_fk_fields = ('session_run', 'template')

    class Meta:
        app_label = 'notes'
        ordering = ['-note_date', '-created_at']

    @property
    def is_editable(self) -> bool:
        return self.status in (self.Status.DRAFT, self.Status.REJECTED)

    @property
    def client_id(self):
        return self.external_client_id

    def __str__(self) -> str:
        return f'Note {self.id} [{self.status}] — {self.external_client_id} {self.note_date}'


class NoteAssignment(TenantAwareModel):
    """
    Admin/supervisor assigns a NoteTemplate to an appointment in the linked external PM system.
    Matches TherapyPMS's docu_seal_templates / session_notes_avails pattern.
    Staff sees these assignments and fills each one; once filled the note FK is set.
    """
    external_appointment_id = models.BigIntegerField(db_index=True)
    external_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
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

    _org_scoped_fk_fields = ('template', 'note')

    class Meta:
        app_label = 'notes'
        # external_appointment_id used to be unique-per-template only because
        # each org had its own schema — two orgs could plausibly share a TPMS
        # appointment id (e.g. cloned staging data), so scope by organization too.
        unique_together = [('organization', 'external_appointment_id', 'template')]
        ordering = ['created_at']

    @property
    def is_filled(self):
        return self.note_id is not None

    def __str__(self):
        return f'Assignment {self.id} — appt {self.external_appointment_id} → {self.template.name}'


class NoteSignature(OrganizationScopedMixin):
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

    def _derive_organization_id(self) -> int | None:
        return self.note.organization_id

    class Meta:
        app_label = 'notes'
        ordering = ['signed_at']

    def __str__(self) -> str:
        return f'{self.signer_name} [{self.signature_type}] @ {self.signed_at:%Y-%m-%d %H:%M}'
