from django.db import models
from shared.models import TenantAwareModel


class PromptingTemplate(TenantAwareModel):
    """
    Defines the scored response levels used during trial data entry.
    Example levels: [{"label": "Full Physical", "score": 0, "color": "#e74c3c", "abbreviation": "FP"}, ...]
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    levels = models.JSONField(default=list)
    is_org_default = models.BooleanField(default=False)

    class Meta:
        app_label = 'programs'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class MasteryTemplate(TenantAwareModel):
    """
    Defines phase-advancement rules applied to targets.
    Example rules: {"consecutive_sessions": 3, "threshold_pct": 80, "minimum_trials": 5}
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    rules = models.JSONField(default=dict)
    is_org_default = models.BooleanField(default=False)

    class Meta:
        app_label = 'programs'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Program(TenantAwareModel):
    class Category(models.TextChoices):
        SKILL_ACQUISITION = 'skill_acquisition', 'Skill Acquisition'
        BEHAVIOR_REDUCTION = 'behavior_reduction', 'Behavior Reduction'
        ABC_RECORDING = 'abc_recording', 'ABC Recording'
        TELEHEALTH = 'telehealth', 'Telehealth'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'
        ARCHIVED = 'archived', 'Archived'

    class Phase(models.TextChoices):
        BASELINE = 'baseline', 'Baseline'
        TEACHING = 'teaching', 'Teaching'
        GENERALIZING = 'generalizing', 'Generalizing'
        MAINTENANCE = 'maintenance', 'Maintenance'
        MASTERED = 'mastered', 'Mastered'
        ON_HOLD = 'on_hold', 'On Hold'

    external_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    is_template = models.BooleanField(default=False, db_index=True)
    name = models.CharField(max_length=200)
    workflow_template = models.ForeignKey(
        'WorkflowTemplate',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='programs',
    )
    maintenance_schedule = models.ForeignKey(
        'MaintenanceSchedule',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='programs',
    )
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.SKILL_ACQUISITION)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    phase = models.CharField(max_length=20, choices=Phase.choices, default=Phase.TEACHING, blank=True)
    treatment_area = models.CharField(max_length=200, blank=True)
    tags = models.JSONField(default=list)
    baseline_notes = models.TextField(blank=True)
    objective = models.TextField(blank=True)
    instructions = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'programs'
        ordering = ['display_order', 'name']

    @property
    def client_id(self):
        return self.external_client_id

    def __str__(self) -> str:
        return f'{self.name} ({self.external_client_id})'


class TargetQuerySet(models.QuerySet):
    def visible_to_staff(self) -> 'TargetQuerySet':
        """Returns only the targets that should appear in the mobile session execution view."""
        return self.filter(
            is_visible_to_staff=True,
            status__in=TargetStatus.objects.filter(is_staff_visible=True).values_list('key', flat=True),
        )


class Target(TenantAwareModel):
    class Status(models.TextChoices):
        WAITING      = 'waiting',      'Waiting'
        PROBE        = 'probe',        'Probe'
        ACQUISITION  = 'acquisition',  'Acquisition'
        MASTERED     = 'mastered',     'Mastered'
        CLOSED       = 'closed',       'Closed'
        HOLD         = 'hold',         'Hold'
        DISCONTINUED = 'discontinued', 'Discontinued'

    class MasteryMode(models.TextChoices):
        MANUAL    = 'manual',    'Manual'
        AUTOMATIC = 'automatic', 'Automatic'

    class MeasurementType(models.TextChoices):
        DISCRETE_TRIAL  = 'discrete_trial',  'Discrete Trial'
        DURATION        = 'duration',        'Duration'
        RATE            = 'rate',            'Rate'
        TASK_ANALYSIS   = 'task_analysis',   'Task Analysis'
        SET_OF_TARGETS  = 'set_of_targets',  'Set of Targets'
        SHAPING         = 'shaping',         'Shaping'
        INSTRUCTIONS    = 'instructions',    'Instructions'
        # Legacy — kept so old rows stay valid
        TRIAL_BY_TRIAL   = 'trial_by_trial',   'Trial by Trial (legacy)'
        FREQUENCY        = 'frequency',        'Frequency (legacy)'
        WHOLE_INTERVAL   = 'whole_interval',   'Whole Interval (legacy)'
        PARTIAL_INTERVAL = 'partial_interval', 'Partial Interval (legacy)'

    objects = TargetQuerySet.as_manager()

    program = models.ForeignKey(
        Program,
        on_delete=models.CASCADE,
        related_name='targets',
    )
    name = models.CharField(max_length=200)
    measurement_type = models.CharField(
        max_length=30,
        choices=MeasurementType.choices,
        default=MeasurementType.DISCRETE_TRIAL,
    )
    # Ordered list of {"key": str, "label": str}. Used by task_analysis (sequential
    # steps), set_of_targets (independent items), and shaping (approximation levels,
    # last entry = terminal/goal level). Unused (empty) by every other measurement type.
    sub_items = models.JSONField(default=list, blank=True)
    prompting_template = models.ForeignKey(
        PromptingTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='targets',
    )
    mastery_template = models.ForeignKey(
        MasteryTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='targets',
    )
    workflow_template = models.ForeignKey(
        'WorkflowTemplate',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='targets',
    )
    maintenance_schedule = models.ForeignKey(
        'MaintenanceSchedule',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='targets',
    )
    maintenance_episodes_completed = models.PositiveIntegerField(default=0)
    sd_text = models.TextField(blank=True, verbose_name='Discriminative Stimulus')
    teaching_instructions = models.TextField(blank=True)
    # No longer choice-constrained — status keys are org-configurable via TargetStatus.
    # Status class above is kept as a reference to the legacy built-in keys (used by the seed migration).
    status = models.CharField(max_length=20, default=Status.WAITING, db_index=True)
    mastery_mode = models.CharField(max_length=10, choices=MasteryMode.choices, default=MasteryMode.MANUAL)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_visible_to_staff = models.BooleanField(default=True)

    class Meta:
        app_label = 'programs'
        ordering = ['display_order', 'id']

    def __str__(self) -> str:
        return f'{self.name} [{self.status}]'


class WorkflowTemplate(TenantAwareModel):
    """
    Defines the ordered phase sequence a target moves through and what triggers each transition.

    phases JSON structure (ordered list):
    [
      {
        "phase": "probe",
        "criteria": {"consecutive_sessions": 1, "threshold_pct": 100, "minimum_trials": 3},
        "on_success": "acquisition",
        "on_regression": null
      },
      {
        "phase": "acquisition",
        "criteria": {"consecutive_sessions": 3, "threshold_pct": 80, "minimum_trials": 5},
        "on_success": "mastered",
        "on_regression": "probe"
      },
      {
        "phase": "mastered",
        "on_success": "maintenance"
      }
    ]
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    phases = models.JSONField(default=list)
    is_org_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'programs'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class MaintenanceSchedule(TenantAwareModel):
    """
    Controls how a mastered target reappears during maintenance before final closure.
    """
    class IntervalType(models.TextChoices):
        EVERY_N_SESSIONS = 'every_n_sessions', 'Every N Sessions'
        WEEKLY = 'weekly', 'Weekly'
        MONTHLY = 'monthly', 'Monthly'

    class OnFailure(models.TextChoices):
        BACK_TO_ACQUISITION = 'back_to_acquisition', 'Back to Acquisition'
        STAY_IN_MAINTENANCE = 'stay_in_maintenance', 'Stay in Maintenance'

    name = models.CharField(max_length=200)
    interval_type = models.CharField(
        max_length=20, choices=IntervalType.choices, default=IntervalType.EVERY_N_SESSIONS
    )
    interval_value = models.PositiveIntegerField(
        default=5,
        help_text='Number of sessions between maintenance appearances (used with every_n_sessions)',
    )
    episodes = models.PositiveIntegerField(
        default=4,
        help_text='Number of successful maintenance episodes before auto-close',
    )
    success_threshold_pct = models.PositiveIntegerField(
        default=80,
        help_text='Minimum % correct to count a maintenance episode as successful',
    )
    on_failure = models.CharField(
        max_length=25, choices=OnFailure.choices, default=OnFailure.BACK_TO_ACQUISITION
    )
    is_org_default = models.BooleanField(default=False)

    class Meta:
        app_label = 'programs'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class TargetStatusChange(TenantAwareModel):
    """
    Immutable audit record of every target status transition.
    created_by is null for automatic (workflow-driven) changes; set for manual changes.
    """
    class Trigger(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        AUTO_MASTERY = 'auto_mastery', 'Automatic — Mastery Criteria Met'

    target = models.ForeignKey(
        Target,
        on_delete=models.CASCADE,
        related_name='status_changes',
    )
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    session_run_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        app_label = 'programs'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'Target {self.target_id}: {self.from_status} → {self.to_status} ({self.trigger})'


class TreatmentArea(TenantAwareModel):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'programs'
        ordering = ['name']

    def __str__(self):
        return self.name


class ProgramTag(TenantAwareModel):
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default='#6366f1')  # hex color
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'programs'
        ordering = ['name']

    def __str__(self):
        return self.name


class TargetStatus(TenantAwareModel):
    """
    Org-configurable status options for Target.status (icon/label/color), replacing
    what used to be a fixed set of 7 statuses. `key` is the literal string stored on
    Target.status — immutable in practice once targets reference it.
    """
    key = models.SlugField(max_length=20)
    label = models.CharField(max_length=50)
    color = models.CharField(max_length=7, default='#6366f1')  # hex color
    icon = models.CharField(max_length=30, default='circle')
    is_staff_visible = models.BooleanField(default=False, help_text='Shown to staff in the session recording view')
    is_default = models.BooleanField(default=False, help_text='Starting status for newly created targets')
    is_active = models.BooleanField(default=True, help_text='Inactive statuses are hidden everywhere except Settings')
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = 'programs'
        ordering = ['display_order', 'label']
        unique_together = [['key']]

    def __str__(self):
        return self.label


class ProgramDataField(TenantAwareModel):
    class FieldType(models.TextChoices):
        TEXT = 'text', 'Text'
        DATE = 'date', 'Date'
        YES_NO = 'yes_no', 'Yes/No'

    class FieldLocation(models.TextChoices):
        TREATMENT_TAB = 'treatment_tab', 'Treatment Tab'
        INSTRUCTIONS_TAB = 'instructions_tab', 'Instructions Tab'

    name = models.CharField(max_length=200)
    field_type = models.CharField(max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    field_location = models.CharField(max_length=30, choices=FieldLocation.choices, default=FieldLocation.TREATMENT_TAB)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'programs'
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class Lesson(TenantAwareModel):
    """
    A curated subset of programs grouped for a specific session type.
    Staff see lessons (not raw program lists) when starting a session.
    """
    class LessonType(models.TextChoices):
        OPEN = 'open', 'Open'
        SCHEDULED = 'scheduled', 'Scheduled'
        APPOINTMENT_LINKED = 'appointment_linked', 'Appointment Linked'

    external_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    name = models.CharField(max_length=200)
    lesson_type = models.CharField(max_length=25, choices=LessonType.choices, default=LessonType.OPEN)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = 'programs'
        ordering = ['name']

    @property
    def client_id(self):
        return self.external_client_id

    def __str__(self) -> str:
        return f'{self.name} ({self.external_client_id})'


class LessonProgram(models.Model):
    """Join table — controls which programs appear in a lesson and in what order."""
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='lesson_programs')
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name='lesson_programs')
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = 'programs'
        unique_together = [['lesson', 'program']]
        ordering = ['display_order']

    def __str__(self) -> str:
        return f'{self.lesson_id} → {self.program_id}'
