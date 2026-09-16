from django.db import models
from shared.models import TenantAwareModel


class GraphAnnotation(TenantAwareModel):
    """
    Supervisor-authored overlays that appear on program and target graphs.

    phase_line  — a vertical line at a specific date (marks a clinical phase change)
    graph_note  — a callout label anchored to a date
    phase_range — a shaded region between two dates (labels a clinical phase period)
    """

    class AnnotationType(models.TextChoices):
        PHASE_LINE = 'phase_line', 'Phase Line'
        GRAPH_NOTE = 'graph_note', 'Graph Note'
        PHASE_RANGE = 'phase_range', 'Phase Range'

    class LineStyle(models.TextChoices):
        SOLID = 'solid', 'Solid'
        DASHED = 'dashed', 'Dashed'
        DOTTED = 'dotted', 'Dotted'

    program = models.ForeignKey(
        'programs.Program',
        on_delete=models.CASCADE,
        related_name='annotations',
    )
    target = models.ForeignKey(
        'programs.Target',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='annotations',
    )
    annotation_type = models.CharField(max_length=20, choices=AnnotationType.choices, db_index=True)
    date = models.DateField(db_index=True)
    end_date = models.DateField(null=True, blank=True)   # phase_range only
    label = models.CharField(max_length=200)
    color = models.CharField(max_length=7, default='#666666')  # hex
    style = models.CharField(max_length=10, choices=LineStyle.choices, default=LineStyle.SOLID)
    notes = models.TextField(blank=True)

    # program is the owning parent (cross-app FK -> programs.Program);
    # target is optional and cross-checked against it.
    _org_scoped_fk_fields = ('target',)

    def _derive_organization_id(self) -> int | None:
        return self.program.organization_id

    class Meta:
        app_label = 'analytics'
        ordering = ['date']

    def __str__(self) -> str:
        return f'{self.annotation_type} — {self.label} ({self.date})'


class ClientAnnotation(TenantAwareModel):
    """
    Supervisor-authored date markers on the client-level Progress screen's
    mastery chart — client-scoped, unlike GraphAnnotation which is anchored
    to one program. Deliberately minimal (a labeled vertical line) since the
    chart aggregates across every program at once.
    """
    external_client_id = models.PositiveIntegerField(db_index=True)
    date = models.DateField(db_index=True)
    label = models.CharField(max_length=200)
    color = models.CharField(max_length=7, default='#666666')  # hex
    style = models.CharField(max_length=10, choices=GraphAnnotation.LineStyle.choices, default=GraphAnnotation.LineStyle.SOLID)
    notes = models.TextField(blank=True)

    class Meta:
        app_label = 'analytics'
        ordering = ['date']

    def __str__(self) -> str:
        return f'{self.label} ({self.date})'


class SavedInsightGraph(TenantAwareModel):
    """
    User-saved graph configuration for the client/program analytics screens.

    The graph options are intentionally stored as JSON so the frontend can add
    new axis, chart, and filter options without requiring a schema migration for
    every graph-builder enhancement.
    """

    class Visibility(models.TextChoices):
        PRIVATE = 'private', 'Only me'
        EVERYONE = 'everyone', 'All users'
        ROLES = 'roles', 'Specific roles'

    external_client_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    program = models.ForeignKey(
        'programs.Program',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='saved_insight_graphs',
    )
    name = models.CharField(max_length=100)
    config = models.JSONField(default=dict)
    visibility = models.CharField(max_length=20, choices=Visibility.choices, default=Visibility.PRIVATE)
    roles = models.JSONField(default=list)
    is_default = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

    _org_scoped_fk_fields = ('program',)

    def _derive_organization_id(self) -> int | None:
        return self.program.organization_id if self.program_id else None

    class Meta:
        app_label = 'analytics'
        ordering = ['display_order', 'name']

    def __str__(self) -> str:
        scope = f'program:{self.program_id}' if self.program_id else f'client:{self.external_client_id}'
        return f'{self.name} ({scope})'


class AssessmentRecord(TenantAwareModel):
    """Client-scoped assessment score captured over time for graphing."""

    external_client_id = models.PositiveIntegerField(db_index=True)
    assessment_name = models.CharField(max_length=120, db_index=True)
    domain = models.CharField(max_length=120, blank=True, db_index=True)
    metric = models.CharField(max_length=120, blank=True, db_index=True)
    assessed_on = models.DateField(db_index=True)
    score = models.FloatField()
    max_score = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        app_label = 'analytics'
        ordering = ['assessed_on', 'assessment_name', 'domain', 'metric']

    def __str__(self) -> str:
        label = ' / '.join(part for part in [self.assessment_name, self.domain, self.metric] if part)
        return f'{label}: {self.score} ({self.assessed_on})'
