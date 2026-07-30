import uuid

from django.conf import settings
from django.db import models


def _central_program_upload_path(instance, filename):
    return f'central_library/programs/{uuid.uuid4().hex}/{filename}'


class CentralProgramFolder(models.Model):
    """Platform-owned grouping for Central Library programs — same
    "not tenant-scoped, superuser-authored" model as CentralProgram itself.
    Importing a whole folder (see apps.programs.api) creates/reuses an
    org-owned ProgramFolder with the same name and files each cloned
    program into it."""
    name = models.CharField(max_length=200, unique=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
    )

    class Meta:
        app_label = 'central_library'
        ordering = ['display_order', 'name']

    def __str__(self) -> str:
        return self.name


class CentralProgram(models.Model):
    """
    Platform-owned reference program, authored by the platform vendor
    (Django admin, superusers only) and importable read-only by every
    Organization. Lives once in the public schema — unlike `apps.programs`,
    this is NOT tenant-scoped: there is exactly one shared catalog, not one
    per organization.

    Importing clones this (+ its targets) into the importing org's own
    `apps.programs.Program`/`Target` rows — see
    `apps.programs.api._clone_central_program`.
    """
    class Category(models.TextChoices):
        SKILL_ACQUISITION = 'skill_acquisition', 'Skill Acquisition'
        BEHAVIOR_REDUCTION = 'behavior_reduction', 'Behavior Reduction'
        ABC_RECORDING = 'abc_recording', 'ABC Recording'
        TELEHEALTH = 'telehealth', 'Telehealth'

    class Phase(models.TextChoices):
        BASELINE = 'baseline', 'Baseline'
        TEACHING = 'teaching', 'Teaching'
        GENERALIZING = 'generalizing', 'Generalizing'
        MAINTENANCE = 'maintenance', 'Maintenance'
        MASTERED = 'mastered', 'Mastered'
        ON_HOLD = 'on_hold', 'On Hold'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ARCHIVED = 'archived', 'Archived'

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.SKILL_ACQUISITION)
    phase = models.CharField(max_length=20, choices=Phase.choices, default=Phase.TEACHING, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    treatment_area = models.CharField(max_length=200, blank=True)
    tags = models.JSONField(default=list, blank=True)
    objective = models.TextField(blank=True)
    instructions = models.TextField(blank=True)
    image = models.ImageField(upload_to=_central_program_upload_path, max_length=500, blank=True, null=True)
    folder = models.ForeignKey(
        CentralProgramFolder,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='programs',
    )
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
    )

    class Meta:
        app_label = 'central_library'
        ordering = ['display_order', 'name']

    def __str__(self) -> str:
        return self.name


class CentralTarget(models.Model):
    class MeasurementType(models.TextChoices):
        DISCRETE_TRIAL = 'discrete_trial', 'Discrete Trial'
        DURATION = 'duration', 'Duration'
        RATE = 'rate', 'Rate'
        TASK_ANALYSIS = 'task_analysis', 'Task Analysis'
        SET_OF_TARGETS = 'set_of_targets', 'Set of Targets'
        SHAPING = 'shaping', 'Shaping'
        INSTRUCTIONS = 'instructions', 'Instructions'

    program = models.ForeignKey(CentralProgram, on_delete=models.CASCADE, related_name='targets')
    name = models.CharField(max_length=200)
    measurement_type = models.CharField(
        max_length=30, choices=MeasurementType.choices, default=MeasurementType.DISCRETE_TRIAL,
    )
    sub_items = models.JSONField(default=list, blank=True)
    sd_text = models.TextField(blank=True, verbose_name='Discriminative Stimulus')
    teaching_instructions = models.TextField(blank=True)
    # Optional starter prompt-level scheme, same shape as
    # apps.programs.PromptingTemplate.levels. On import this becomes a new
    # org-owned PromptingTemplate — PromptingTemplate is tenant-scoped, so a
    # shared FK to one central row isn't possible (and wouldn't be desired;
    # each org should own and be able to edit its own copy).
    prompting_levels = models.JSONField(default=list, blank=True)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_visible_to_staff = models.BooleanField(default=True)

    class Meta:
        app_label = 'central_library'
        ordering = ['display_order', 'id']

    def __str__(self) -> str:
        return self.name
