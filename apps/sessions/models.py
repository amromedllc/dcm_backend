from django.conf import settings
from django.db import models
from shared.models import TenantAwareModel


class Appointment(TenantAwareModel):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        NO_SHOW = 'no_show', 'No Show'

    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        SYNCED = 'synced', 'Synced from PM System'

    tpms_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
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

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['-start_time']

    @property
    def client_id(self):
        return self.tpms_client_id

    def __str__(self) -> str:
        return f'{self.tpms_client_id} | {self.start_time:%Y-%m-%d %H:%M}'


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

    tpms_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    tpms_appointment_id = models.BigIntegerField(null=True, blank=True, db_index=True)
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

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['-started_at']

    @property
    def client_id(self):
        return self.tpms_client_id

    @property
    def appointment_id(self):
        return self.tpms_appointment_id

    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.OPEN

    def __str__(self) -> str:
        return f'Session {self.id} [{self.status}] — client {self.tpms_client_id}'


class TrialEvent(models.Model):
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
    recorded_at = models.DateTimeField(db_index=True)
    staff_notes = models.TextField(blank=True)

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['target_id', 'trial_number']
        unique_together = [['session_run', 'target_id', 'trial_number']]

    def __str__(self) -> str:
        return f'Trial {self.trial_number} — target {self.target_id} [{self.prompt_level_label}]'


class BehaviorEvent(models.Model):
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

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['occurred_at']

    def __str__(self) -> str:
        return f'Behavior {self.target_name} @ {self.occurred_at:%H:%M}'


class ABCEvent(models.Model):
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

    class Meta:
        app_label = 'dcm_sessions'
        ordering = ['occurred_at']

    def __str__(self) -> str:
        return f'ABC @ {self.occurred_at:%H:%M} — {self.behavior_description[:40]}'
