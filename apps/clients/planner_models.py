"""Private structured drafts. Clinical finalization ships in Stage 4."""
from django.core.exceptions import ValidationError
from django.db import models
from shared.models import TenantAwareModel
from shared.tenancy import TenantManager, TenantQuerySet, current_org_id
from apps.integrations.practice_planner.contracts import Category


class ValidatedPlannerQuerySet(TenantQuerySet):
    def update(self, **kwargs):
        raise ValidationError('Use validated planner model saves')

    def bulk_create(self, *args, **kwargs):
        raise ValidationError('Use validated planner model saves')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Use validated planner model saves')


class PlannerModel(TenantAwareModel):
    organization = models.ForeignKey('tenants.Organization', on_delete=models.CASCADE)
    immutable_relation_fields = ()
    objects = TenantManager.from_queryset(ValidatedPlannerQuerySet)()
    all_organizations = models.Manager.from_queryset(ValidatedPlannerQuerySet)()

    class Meta:
        abstract = True
        base_manager_name = 'objects'

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization_id = self._derive_organization_id()
        if self.organization_id != current_org_id():
            raise ValidationError('Planner organization does not match tenant context')
        self._validate_cross_org_fks()
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, field + '_id') != getattr(self, field + '_id')
                   for field in self.immutable_relation_fields):
                raise ValidationError('Planner ownership and selected source are immutable')
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{type(self).__name__} #{self.pk}'


class ClientDiagnosis(PlannerModel):
    immutable_relation_fields = ('client', 'organization')
    lock_version = models.PositiveIntegerField(default=1)
    class ClinicalStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        RESOLVED = 'resolved', 'Resolved'
        INACTIVE = 'inactive', 'Inactive'

    client = models.ForeignKey('clients.Client', on_delete=models.CASCADE, related_name='diagnoses')
    code_system = models.CharField(max_length=30, choices=[(s, s) for s in ('ICD-10-CM', 'DSM-5', 'SYNTHETIC')])
    code_version = models.CharField(max_length=80)
    code = models.CharField(max_length=80)
    display = models.CharField(max_length=500)
    clinical_status = models.CharField(max_length=20, choices=ClinicalStatus.choices, default=ClinicalStatus.ACTIVE)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    supersedes = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='superseded_by')
    _org_scoped_fk_fields = ('client', 'supersedes')

    def _derive_organization_id(self):
        return self.client.organization_id

    def clean(self):
        super().clean()
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError('Invalid diagnosis effective dates')
        if self.supersedes_id:
            if self.supersedes_id == self.pk or self.supersedes.client_id != self.client_id:
                raise ValidationError('Superseded diagnosis must be a different diagnosis of the same client')
            seen = {self.pk} if self.pk else set()
            previous = self.supersedes
            while previous:
                if previous.pk in seen:
                    raise ValidationError('Diagnosis supersession cannot form a cycle')
                seen.add(previous.pk)
                previous = previous.supersedes


def validate_draft(plan):
    if plan.planning_mode != 'structured' or plan.status != 'draft' or plan.is_signed:
        raise ValidationError('Structured rows require an unsigned structured draft')


class TreatmentPlanDiagnosis(PlannerModel):
    plan = models.ForeignKey('clients.TreatmentPlan', on_delete=models.CASCADE, related_name='diagnosis_selections')
    diagnosis = models.ForeignKey(ClientDiagnosis, on_delete=models.PROTECT, related_name='plan_selections')
    code_system = models.CharField(max_length=30)
    code_version = models.CharField(max_length=80)
    code = models.CharField(max_length=80)
    display = models.CharField(max_length=500)
    _org_scoped_fk_fields = ('plan', 'diagnosis')

    class Meta(PlannerModel.Meta):
        constraints = [models.UniqueConstraint(fields=['plan', 'diagnosis'], name='planner_unique_diagnosis_selection')]

    def _derive_organization_id(self):
        return self.plan.organization_id

    def clean(self):
        super().clean()
        validate_draft(self.plan)
        if self.diagnosis.client_id != self.plan.client_id:
            raise ValidationError('Diagnosis must belong to the plan client')
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, f) != getattr(self, f) for f in ('plan_id', 'diagnosis_id', 'code_system', 'code_version', 'code', 'display')):
                raise ValidationError('Diagnosis selection snapshots are immutable')

    def save(self, *args, **kwargs):
        if self._state.adding:
            for field in ('code_system', 'code_version', 'code', 'display'):
                setattr(self, field, getattr(self.diagnosis, field))
        return super().save(*args, **kwargs)


class StructuredRow(PlannerModel):
    source = models.ForeignKey('integrations.PlannerContentSnapshot', on_delete=models.PROTECT, related_name='+')
    clinician_text = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)

    @property
    def immutable_relation_fields(self):
        return (*self._org_scoped_fk_fields, 'organization')

    class Meta(PlannerModel.Meta):
        abstract = True
        ordering = ['position', 'id']

    def _derive_organization_id(self):
        return self.owning_plan.organization_id

    def clean(self):
        super().clean()
        validate_draft(self.owning_plan)
        if self.source.category != self.source_category:
            raise ValidationError('Incorrect source category')
        if self.source.normalized_payload.get('retired'):
            raise ValidationError('Retired content cannot be newly selected')
        parent = self.content_parent
        if parent:
            for field in ('provider', 'planner_id', 'edition', 'version', 'language'):
                if getattr(self.source, field) != getattr(parent.source, field):
                    raise ValidationError('Source edition and provider must match parent')
            if parent.source.component_id not in self.source.normalized_payload['parent_ids']:
                raise ValidationError('Source does not belong to the selected content parent')


class TreatmentPlanProblem(StructuredRow):
    plan = models.ForeignKey('clients.TreatmentPlan', on_delete=models.CASCADE, related_name='problems')
    _org_scoped_fk_fields = ('plan', 'source')
    source_category = Category.PROBLEM
    content_parent = None

    @property
    def owning_plan(self):
        return self.plan


class TreatmentPlanGoal(StructuredRow):
    problem = models.ForeignKey(TreatmentPlanProblem, on_delete=models.CASCADE, related_name='goals')
    _org_scoped_fk_fields = ('problem', 'source')
    source_category = Category.GOAL

    @property
    def owning_plan(self):
        return self.problem.plan

    @property
    def content_parent(self):
        return self.problem


class TreatmentPlanObjective(StructuredRow):
    goal = models.ForeignKey(TreatmentPlanGoal, on_delete=models.CASCADE, related_name='objectives')
    _org_scoped_fk_fields = ('goal', 'source')
    source_category = Category.OBJECTIVE

    @property
    def owning_plan(self):
        return self.goal.problem.plan

    @property
    def content_parent(self):
        return self.goal


class TreatmentPlanIntervention(StructuredRow):
    objective = models.ForeignKey(TreatmentPlanObjective, on_delete=models.CASCADE, related_name='interventions')
    _org_scoped_fk_fields = ('objective', 'source')
    source_category = Category.INTERVENTION

    @property
    def owning_plan(self):
        return self.objective.goal.problem.plan

    @property
    def content_parent(self):
        return self.objective
