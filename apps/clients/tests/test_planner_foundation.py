import json
import uuid
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.clients.models import (
    Client, ClientDiagnosis, TreatmentPlan, TreatmentPlanDiagnosis, TreatmentPlanGoal,
    TreatmentPlanIntervention, TreatmentPlanObjective, TreatmentPlanProblem, TreatmentPlanSignature,
)
from apps.integrations.models import PlannerContentSnapshot
from apps.integrations.practice_planner.mock import MockPracticePlannerProvider
from apps.integrations.practice_planner.snapshots import retain_selection
from apps.tenants.models import Organization
from shared.tenancy import CrossOrganizationReferenceError, TenantContextError, tenant_context


class PlannerFoundationTests(TestCase):
    def setUp(self):
        # Avoid TenantMixin.save() provisioning schemas in fast SQLite tests.
        self.org, self.other_org = Organization.objects.bulk_create([
            Organization(name='Synthetic A', slug='planner-a', schema_name='planner_a'),
            Organization(name='Synthetic B', slug='planner-b', schema_name='planner_b'),
        ])
        self.context = tenant_context(self.org.pk)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.client_row = Client.objects.create(first_name='Synthetic', last_name='A')
        self.other_client = Client.objects.create(first_name='Synthetic', last_name='B')
        self.plan = TreatmentPlan.objects.create(client=self.client_row, title='Synthetic draft',
                                                plan_date=date(2026, 1, 1), planning_mode='structured')
        self.provider = MockPracticePlannerProvider()

    def source(self, key, edition='test-1'):
        return retain_selection(self.provider.get_component(key, edition=edition))

    def diagnosis(self, **changes):
        values = dict(client=self.client_row, code_system='SYNTHETIC', code_version='test',
                      code='SYNTHETIC-001', display='Original synthetic diagnosis')
        values.update(changes)
        return ClientDiagnosis.objects.create(**values)

    def hierarchy(self):
        problem = TreatmentPlanProblem.objects.create(plan=self.plan, source=self.source('p1'))
        goal = TreatmentPlanGoal.objects.create(problem=problem, source=self.source('g1'))
        objective = TreatmentPlanObjective.objects.create(goal=goal, source=self.source('o1'))
        intervention = TreatmentPlanIntervention.objects.create(objective=objective, source=self.source('i1'))
        return problem, goal, objective, intervention

    def test_complete_hierarchy_and_separate_clinician_wording(self):
        problem, goal, objective, intervention = self.hierarchy()
        intervention.clinician_text = 'Synthetic clinician revision'
        intervention.save()
        self.assertEqual(self.plan.problems.get().goals.get().objectives.get().interventions.get(), intervention)
        self.assertNotEqual(intervention.clinician_text, intervention.source.text)
        self.assertEqual({r.organization_id for r in (problem, goal, objective, intervention)}, {self.org.pk})

    def test_snapshot_idempotency_editions_and_protected_retention(self):
        old = self.source('i1')
        self.assertEqual(self.source('i1').pk, old.pk)
        new = self.source('i1', 'test-2')
        self.assertNotEqual(old.pk, new.pk)
        self.assertNotEqual(old.digest, new.digest)
        self.hierarchy()
        with self.assertRaises(ValidationError):
            old.delete()

    def test_snapshot_instance_queryset_and_bulk_mutations_denied(self):
        source = self.source('p1')
        for operation in (
            lambda: source.save(), lambda: source.delete(),
            lambda: PlannerContentSnapshot.objects.filter(pk=source.pk).update(text='Changed'),
            lambda: PlannerContentSnapshot.objects.filter(pk=source.pk).delete(),
            lambda: PlannerContentSnapshot.objects.bulk_update([source], ['text']),
            lambda: PlannerContentSnapshot.objects.bulk_create([source]),
            lambda: PlannerContentSnapshot.all_organizations.filter(pk=source.pk).update(text='Changed'),
        ):
            with self.assertRaises(ValidationError):
                operation()

    def test_snapshot_tampering_rejected(self):
        source = self.source('p1')
        source.pk = None
        source._state.adding = True
        source.text = 'Mismatched source text'
        with self.assertRaises(ValidationError):
            source.save()

    def test_snapshot_existing_primary_key_cannot_be_overwritten_on_insert(self):
        original = self.source('p1')
        values = {f.attname: getattr(original, f.attname) for f in original._meta.concrete_fields}
        replacement = PlannerContentSnapshot(**values)
        with self.assertRaises(ValidationError):
            replacement.save()
        original.refresh_from_db()
        self.assertEqual(original.text, self.provider.get_component('p1', edition='test-1').text)

    def test_invalid_category_parent_and_edition_rejected(self):
        problem, goal, objective, _ = self.hierarchy()
        for source in (self.source('i1'), self.source('g1', 'test-2')):
            with self.assertRaises(ValidationError):
                TreatmentPlanGoal.objects.create(problem=problem, source=source)
        content = replace(self.provider.get_component('g1', edition='test-1'), parent_ids=('other-problem',))
        with self.assertRaises(ValidationError):
            TreatmentPlanGoal.objects.create(problem=problem, source=retain_selection(content))
        with self.assertRaises(ValidationError):
            TreatmentPlanIntervention.objects.create(objective=objective, source=self.source('i2', 'test-2'))

    def test_reparenting_selected_rows_and_bulk_bypass_rejected(self):
        problem, goal, _, _ = self.hierarchy()
        other = TreatmentPlanProblem.objects.create(plan=self.plan, source=self.source('p2'))
        goal.problem = other
        with self.assertRaises(ValidationError):
            goal.save()
        with self.assertRaises(ValidationError):
            TreatmentPlanGoal.objects.update(problem=other)

    def test_diagnosis_wording_snapshot_survives_diagnosis_edits(self):
        diagnosis = self.diagnosis()
        selected = TreatmentPlanDiagnosis.objects.create(plan=self.plan, diagnosis=diagnosis)
        diagnosis.display = 'Updated synthetic diagnosis'
        diagnosis.save()
        selected.refresh_from_db()
        self.assertEqual(selected.display, 'Original synthetic diagnosis')
        selected.display = 'Changed snapshot'
        with self.assertRaises(ValidationError):
            selected.save()

    def test_diagnosis_same_client_dates_system_and_cycles(self):
        with self.assertRaises(ValidationError):
            TreatmentPlanDiagnosis.objects.create(plan=self.plan, diagnosis=self.diagnosis(client=self.other_client))
        for changes in ({'code_system': ''}, {'effective_from': date(2026, 2, 1), 'effective_to': date(2026, 1, 1)}):
            with self.assertRaises(ValidationError):
                self.diagnosis(**changes)
        original = self.diagnosis()
        replacement = self.diagnosis(supersedes=original)
        original.supersedes = replacement
        with self.assertRaises(ValidationError):
            original.save()

    def test_cross_organization_references_and_reads_rejected(self):
        source = self.source('p1')
        with tenant_context(self.other_org.pk):
            self.assertFalse(PlannerContentSnapshot.objects.filter(pk=source.pk).exists())
            self.assertFalse(ClientDiagnosis.objects.exists())
            foreign_client = Client.objects.create(first_name='Synthetic', last_name='Other tenant')
            foreign_source = self.source('p1')
            self.assertNotEqual(source.pk, foreign_source.pk)
        with self.assertRaises((ValidationError, CrossOrganizationReferenceError)):
            TreatmentPlanProblem.objects.create(plan=self.plan, source=foreign_source)
        with self.assertRaises((ValidationError, CrossOrganizationReferenceError)):
            self.diagnosis(client=foreign_client)
        with self.assertRaises(ValidationError):
            TreatmentPlan.objects.create(client=foreign_client, title='Invalid tenant',
                planning_mode='structured', plan_date=date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            self.diagnosis(organization=self.other_org)

    def test_no_context_never_returns_unscoped_content(self):
        with tenant_context(None):
            for model in (ClientDiagnosis, TreatmentPlanProblem, PlannerContentSnapshot):
                with self.assertRaises(TenantContextError):
                    model.objects.count()

    def test_valid_revision_and_immutable_identity(self):
        revision = TreatmentPlan.objects.create(client=self.client_row, title='Revision 2', plan_date=date(2026, 2, 1),
            planning_mode='structured', plan_series_id=self.plan.plan_series_id, revision_number=2, previous_revision=self.plan)
        self.assertEqual(revision.previous_revision, self.plan)
        revision.plan_series_id = uuid.uuid4()
        with self.assertRaises(ValidationError):
            revision.save()

    def test_invalid_revision_links_and_duplicate_numbers(self):
        base = dict(client=self.client_row, title='Invalid revision', plan_date=date(2026, 2, 1), planning_mode='structured',
                    plan_series_id=self.plan.plan_series_id, revision_number=2, previous_revision=self.plan)
        for changes in ({'client': self.other_client}, {'plan_series_id': uuid.uuid4()},
                        {'revision_number': 1}, {'previous_revision': None}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                TreatmentPlan.objects.create(**{**base, **changes})
        TreatmentPlan.objects.create(**base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TreatmentPlan.objects.create(**base)

    def test_existing_analytics_content_and_signatures_unchanged(self):
        plan = TreatmentPlan.objects.create(client=self.client_row, title='Legacy analytics', plan_date=date(2026, 1, 1),
            sections={'goals': ['Synthetic original']}, source_snapshot={'report': 'Synthetic report'}, status='finalized')
        signature = TreatmentPlanSignature.objects.create(plan=plan, signer_id=12, signer_name='Synthetic signer', signer_role='supervisor',
            signature_data='Synthetic signature', content_hash='a' * 64)
        plan.title = 'Existing permitted model edit'
        plan.save()
        plan.refresh_from_db()
        signature.refresh_from_db()
        self.assertEqual(plan.planning_mode, 'analytics')
        self.assertEqual(plan.sections, {'goals': ['Synthetic original']})
        self.assertEqual(plan.source_snapshot, {'report': 'Synthetic report'})
        self.assertEqual(signature.content_hash, 'a' * 64)
        with self.assertRaises(ValidationError):
            TreatmentPlanProblem.objects.create(plan=plan, source=self.source('p1'))

    def test_structured_finalization_is_now_open_but_content_is_frozen_afterward(self):
        self.hierarchy()
        self.plan.status = 'finalized'
        self.plan.save()
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, 'finalized')
        self.plan.title = 'Attempted bypass after finalization'
        with self.assertRaises(ValidationError):
            self.plan.save()
        self.plan.refresh_from_db()
        self.plan.status = 'draft'
        with self.assertRaises(ValidationError):
            self.plan.save()
        problem = self.plan.problems.get()
        problem.clinician_text = 'Attempted structured-row edit after finalization'
        with self.assertRaises(ValidationError):
            problem.save()

    def test_audit_is_metadata_only_and_discarded_on_rollback(self):
        with patch('apps.integrations.practice_planner.audit.log_event') as audit:
            with self.captureOnCommitCallbacks(execute=True):
                self.diagnosis()
                self.hierarchy()
            self.assertTrue(audit.called)
            for call in audit.call_args_list:
                self.assertEqual(call.args[4], {'organization_id': self.org.pk})
                self.assertIsInstance(call.args[3], int)
            serialized = json.dumps(audit.call_args_list, default=str)
            self.assertNotIn('Original synthetic diagnosis', serialized)
            self.assertNotIn('TEST ONLY', serialized)
            audit.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self.diagnosis()
                        raise RuntimeError('Synthetic rollback')
                except RuntimeError:
                    pass
            audit.assert_not_called()
