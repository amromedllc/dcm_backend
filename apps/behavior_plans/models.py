from django.conf import settings
from django.db import models
from shared.models import TenantAwareModel


class BehaviorInterventionPlan(TenantAwareModel):
    """
    A standing, versioned clinical document describing a client's target
    behavior, its function, replacement behaviors, prevention strategies,
    staff response procedures, and crisis/safety escalation steps.

    Each revision is its own row (`previous_version` chains them), so past
    plans stay fully readable for audit even after a new one is authored.

    Scoped to a `Program` (specifically a behavior-reduction one), not
    directly to a `Client` — a client can have several distinct problem
    behaviors (e.g. separate "Self-Injurious Behavior" and "Tantrum
    Behavior" programs), each needing its own plan with its own
    antecedents/replacement-behavior/crisis-steps. Only one row per
    *program* may be `status='active'` at a time — enforced by the partial
    unique constraint below; `services.activate_bip_version()` archives the
    prior active row for that program first so the constraint is never
    tripped. `Program.external_client_id` is always the real `Client.id`
    (set directly from it on program creation — see `programs/api.py`), so
    `client_id` below is a safe, direct alias, not the looser
    TPMS-external-id convention used by `Appointment`/`SessionRun`.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SUBMITTED = 'submitted', 'Submitted for Review'
        ACTIVE = 'active', 'Active'
        REJECTED = 'rejected', 'Rejected'
        ARCHIVED = 'archived', 'Archived'

    class Function(models.TextChoices):
        ESCAPE = 'escape', 'Escape / Avoidance'
        ATTENTION = 'attention', 'Attention'
        TANGIBLE = 'tangible', 'Access to Tangible'
        SENSORY = 'sensory', 'Automatic / Sensory'
        MULTIPLE = 'multiple', 'Multiple / Combined'
        UNKNOWN = 'unknown', 'Undetermined'

    # --- Identity / versioning ---------------------------------------------
    program = models.ForeignKey(
        'programs.Program',
        on_delete=models.CASCADE,
        related_name='behavior_intervention_plans',
    )
    version_number = models.PositiveIntegerField(default=1)
    previous_version = models.OneToOneField(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='next_version',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    title = models.CharField(max_length=200, blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='authored_behavior_plans',
        db_constraint=False,
    )
    # Snapshot, survives account deletion/renaming — same reasoning as
    # TargetPromptLevelChange storing both index and label (programs/models.py).
    author_name = models.CharField(max_length=200, blank=True)
    effective_date = models.DateField(null=True, blank=True, db_index=True)
    review_due_date = models.DateField(null=True, blank=True, db_index=True)

    # --- Clinical content ----------------------------------------------------
    target_behavior_name = models.CharField(max_length=200, blank=True)
    target_behavior_definition = models.TextField(blank=True)
    baseline_summary = models.TextField(blank=True)
    hypothesized_function = models.CharField(
        max_length=20, choices=Function.choices, default=Function.UNKNOWN,
    )
    function_hypothesis_statement = models.TextField(blank=True)
    antecedent_strategies = models.TextField(blank=True)
    replacement_behaviors = models.TextField(blank=True)
    teaching_procedures = models.TextField(blank=True)
    reinforcement_strategies = models.TextField(blank=True)
    response_procedures = models.TextField(blank=True)
    # The one field allowed to hold RichTextEditor HTML (bullet-formatted
    # crisis steps read under pressure benefit from real list formatting).
    crisis_plan = models.TextField(blank=True)
    safety_precautions = models.TextField(blank=True)
    data_collection_plan = models.TextField(blank=True)
    generalization_plan = models.TextField(blank=True)

    # --- Status-transition audit fields (snapshot `_by_id`, not FK — see
    # LessonNote.approved_by_id for the same "survives account changes"
    # reasoning) ---------------------------------------------------------------
    submitted_at = models.DateTimeField(null=True, blank=True)
    activated_by_id = models.IntegerField(null=True, blank=True, db_index=True)
    activated_by_name = models.CharField(max_length=200, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    rejected_by_id = models.IntegerField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    archived_by_id = models.IntegerField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archive_reason = models.TextField(blank=True)

    _org_scoped_fk_fields = ('program', 'previous_version')

    def _derive_organization_id(self) -> int | None:
        return self.program.organization_id

    class Meta:
        app_label = 'behavior_plans'
        ordering = ['-version_number', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['program'],
                condition=models.Q(status='active'),
                name='unique_active_bip_per_program',
            ),
            models.UniqueConstraint(
                fields=['program', 'version_number'],
                name='unique_bip_version_number_per_program',
            ),
        ]
        indexes = [models.Index(fields=['program', 'status'])]

    @property
    def is_editable(self) -> bool:
        return self.status in (self.Status.DRAFT, self.Status.REJECTED)

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def client_id(self) -> int | None:
        return self.program.external_client_id

    def __str__(self) -> str:
        return f'BIP v{self.version_number} [{self.status}] — program {self.program_id}'
