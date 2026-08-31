"""
get_program_mastery_criteria — resolves the % correct mastery threshold from
the program's targets' WorkflowTemplates, for the graph's optional
"Mastery Criteria" reference line.
"""
from django.test import TestCase
from django_tenants.utils import schema_context

from apps.analytics.services import get_program_mastery_criteria
from apps.programs.models import Program, Target, WorkflowTemplate
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


_PHASES_80 = [
    {'phase': 'probe', 'criteria': {'threshold_pct': 100}, 'on_success': 'acquisition'},
    {'phase': 'acquisition', 'criteria': {'threshold_pct': 80}, 'on_success': 'mastered'},
    {'phase': 'mastered', 'on_success': 'maintenance'},
]
_PHASES_90 = [
    {'phase': 'acquisition', 'criteria': {'threshold_pct': 90}, 'on_success': 'mastered'},
]
_PHASES_NO_CRITERIA = [
    {'phase': 'acquisition', 'on_success': 'mastered'},
]


class MasteryCriteriaTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='MC Org', slug='mc-org', schema_name='mc_org',
        )

    def _program(self):
        return Program.objects.create(name='P', category='skill_acquisition', external_client_id=1)

    def test_none_when_no_target_has_a_workflow(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            program = self._program()
            Target.objects.create(program=program, name='T1', measurement_type='discrete_trial')
            self.assertEqual(get_program_mastery_criteria(program.id), (None, False))

    def test_reads_threshold_of_transition_into_mastered(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            program = self._program()
            wf = WorkflowTemplate.objects.create(name='WF 80', phases=_PHASES_80)
            Target.objects.create(
                program=program, name='T1', measurement_type='discrete_trial', workflow_template=wf,
            )
            self.assertEqual(get_program_mastery_criteria(program.id), (80, False))

    def test_varies_flag_and_most_common_value(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            program = self._program()
            wf80 = WorkflowTemplate.objects.create(name='WF 80', phases=_PHASES_80)
            wf90 = WorkflowTemplate.objects.create(name='WF 90', phases=_PHASES_90)
            Target.objects.create(program=program, name='T1', measurement_type='discrete_trial', workflow_template=wf80)
            Target.objects.create(program=program, name='T2', measurement_type='discrete_trial', workflow_template=wf80)
            Target.objects.create(program=program, name='T3', measurement_type='discrete_trial', workflow_template=wf90)
            self.assertEqual(get_program_mastery_criteria(program.id), (80, True))

    def test_ignores_workflow_without_threshold(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            program = self._program()
            wf = WorkflowTemplate.objects.create(name='WF none', phases=_PHASES_NO_CRITERIA)
            Target.objects.create(
                program=program, name='T1', measurement_type='discrete_trial', workflow_template=wf,
            )
            self.assertEqual(get_program_mastery_criteria(program.id), (None, False))
