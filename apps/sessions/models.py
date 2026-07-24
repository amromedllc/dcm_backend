from django.conf import settings
from django.db import models
from shared.models import OrganizationScopedMixin, TenantAwareModel


def _session_media_upload_path(instance, filename):
    return f'session_media/{instance.session_run_id}/{filename}'


class Appointment(TenantAwareModel):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        NO_SHOW = 'no_show', 'No Show'

    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        SYNCED = 'synced', 'Synced from PM System'

    external_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='appointments',
        db_constraint=False,
    )
    lesson = models.ForeignKey(
        'programs.Lesson',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )
    external_id = models.CharField(max_length=100, blank=True, db_index=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    service_type = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    synced_at = models.DateTimeField(null=True, blank=True)

    _org_scoped_fk_fields = ('lesson',)  # cross-app FK -> programs.Lesson

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['-start_time']

    @property
    def client_id(self):
        return self.external_client_id

    def __str__(self) -> str:
        return f'{self.external_client_id} | {self.start_time:%Y-%m-%d %H:%M}'


class SessionRun(TenantAwareModel):
    """
    A single executed session.

    program_snapshot captures the full program+target configuration at the moment
    the session was started. Historical reports always read from the snapshot, never
    from the live program tables, so supervisor edits after the fact cannot corrupt
    audit data.
    """

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        SUBMITTED = 'submitted', 'Submitted'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    external_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    external_appointment_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='session_runs',
        db_constraint=False,
    )
    lesson = models.ForeignKey(
        'programs.Lesson',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='session_runs',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_sessions',
        db_constraint=False,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    # Immutable snapshot taken at session start — the source of truth for reporting
    program_snapshot = models.JSONField(default=dict)

    _org_scoped_fk_fields = ('lesson',)  # cross-app FK -> programs.Lesson

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['-started_at']

    @property
    def client_id(self):
        return self.external_client_id

    @property
    def appointment_id(self):
        return self.external_appointment_id

    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.OPEN

    def __str__(self) -> str:
        return f'Session {self.id} [{self.status}] — client {self.external_client_id}'


class TrialEvent(OrganizationScopedMixin):
    """
    One scored trial for a target within a session.
    Immutable after session submission — never update, only insert.

    target_id / target_name are snapshot values: readable even if the live
    Target row is later archived or renamed.
    """
    session_run = models.ForeignKey(
        SessionRun,
        on_delete=models.CASCADE,
        related_name='trial_events',
    )
    target_id = models.PositiveIntegerField(db_index=True)
    target_name = models.CharField(max_length=200)
    trial_number = models.PositiveIntegerField()
    response_score = models.IntegerField()
    prompt_level_label = models.CharField(max_length=100)
    # Blank for a plain single-target trial (discrete_trial). Set to one of the
    # target's sub_items[].key for task_analysis/set_of_targets/shaping — multiple
    # rows then share one trial_number, together representing a single "pass".
    sub_item_key = models.CharField(max_length=100, blank=True, default='')
    recorded_at = models.DateTimeField(db_index=True)
    staff_notes = models.TextField(blank=True)

    def _derive_organization_id(self) -> int | None:
        return self.session_run.organization_id

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['target_id', 'trial_number']
        unique_together = [['session_run', 'target_id', 'trial_number', 'sub_item_key']]

    def __str__(self) -> str:
        return f'Trial {self.trial_number} — target {self.target_id} [{self.prompt_level_label}]'


class BehaviorEvent(OrganizationScopedMixin):
    """
    A behavior-reduction data point recorded during a session.
    target_id / target_name are snapshot values.
    """

    class Severity(models.TextChoices):
        MILD = 'mild', 'Mild'
        MODERATE = 'moderate', 'Moderate'
        SEVERE = 'severe', 'Severe'

    session_run = models.ForeignKey(
        SessionRun,
        on_delete=models.CASCADE,
        related_name='behavior_events',
    )
    target_id = models.PositiveIntegerField(db_index=True)
    target_name = models.CharField(max_length=200)
    occurred_at = models.DateTimeField(db_index=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    frequency_count = models.PositiveIntegerField(default=1)
    severity = models.CharField(max_length=10, choices=Severity.choices, blank=True)
    notes = models.TextField(blank=True)
    client_event_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    def _derive_organization_id(self) -> int | None:
        return self.session_run.organization_id

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['occurred_at']
        constraints = [
            models.UniqueConstraint(
                fields=['session_run', 'client_event_id'],
                condition=models.Q(client_event_id__isnull=False),
                name='unique_behaviorevent_session_client_event_id',
            ),
        ]

    def __str__(self) -> str:
        return f'Behavior {self.target_name} @ {self.occurred_at:%H:%M}'


class ABCEvent(OrganizationScopedMixin):
    """Antecedent–Behavior–Consequence data entry."""
    session_run = models.ForeignKey(
        SessionRun,
        on_delete=models.CASCADE,
        related_name='abc_events',
    )
    occurred_at = models.DateTimeField(db_index=True)
    antecedent = models.TextField()
    behavior_description = models.TextField()
    consequence = models.TextField()
    setting = models.CharField(max_length=200, blank=True)
    staff_response = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    # See BehaviorEvent.client_event_id — same crash-window dedup purpose.
    client_event_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    def _derive_organization_id(self) -> int | None:
        return self.session_run.organization_id

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['occurred_at']
        constraints = [
            models.UniqueConstraint(
                fields=['session_run', 'client_event_id'],
                condition=models.Q(client_event_id__isnull=False),
                name='unique_abcevent_session_client_event_id',
            ),
        ]

    def __str__(self) -> str:
        return f'ABC @ {self.occurred_at:%H:%M} — {self.behavior_description[:40]}'


class SessionMedia(TenantAwareModel):
    """
    A photo or video attached to a session, optionally scoped to one target
    within it — supports HIPAA-relevant documentation and asynchronous
    supervision review (a supervisor watches later and leaves timestamped
    comments via SessionMediaComment, rather than needing to be on-site).

    Storage: `file` goes through Django's configured storage backend —
    local filesystem in dev, S3 in production (see config/settings/production.py)
    — same mechanism Export already relies on for generated files.
    """
    class MediaType(models.TextChoices):
        PHOTO = 'photo', 'Photo'
        VIDEO = 'video', 'Video'

    class ReviewStatus(models.TextChoices):
        PENDING = 'pending', 'Pending Review'
        REVIEWED = 'reviewed', 'Reviewed'

    session_run = models.ForeignKey(
        SessionRun,
        on_delete=models.CASCADE,
        related_name='media',
    )
    # Snapshot value, same reasoning as TrialEvent.target_name — readable even
    # if the live Target row is later archived or renamed. Null = whole-session clip.
    target_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    target_name = models.CharField(max_length=200, blank=True)
    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    file = models.FileField(upload_to=_session_media_upload_path, max_length=500)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)  # video only
    caption = models.CharField(max_length=300, blank=True)
    review_status = models.CharField(
        max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.PENDING, db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_session_media',
        db_constraint=False,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    _org_scoped_fk_fields = ('session_run',)

    def _derive_organization_id(self) -> int | None:
        return self.session_run.organization_id

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.media_type} on session {self.session_run_id} [{self.review_status}]'


class SessionMediaComment(TenantAwareModel):
    """
    Timestamped supervisor feedback on a SessionMedia clip — the async-review
    equivalent of leaning over an RBT's shoulder mid-session. `timestamp_seconds`
    anchors the comment to a moment in the video; null for a general/photo comment.
    """
    session_media = models.ForeignKey(
        SessionMedia,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    timestamp_seconds = models.PositiveIntegerField(null=True, blank=True)
    body = models.TextField()

    _org_scoped_fk_fields = ('session_media',)

    def _derive_organization_id(self) -> int | None:
        return self.session_media.organization_id

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['created_at']

    def __str__(self) -> str:
        return f'Comment on media {self.session_media_id} @ {self.timestamp_seconds}s'
