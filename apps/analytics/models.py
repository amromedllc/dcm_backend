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

    class Meta:
        app_label = 'analytics'
        ordering = ['date']

    def __str__(self) -> str:
        return f'{self.annotation_type} — {self.label} ({self.date})'
