"""Stage 4: finalize/sign/revise/activate for structured treatment plans."""
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.clients import planner_api as api
from apps.clients.models import Client, ClientDiagnosis, PlanActivation, TreatmentPlan, TreatmentPlanSignature
from apps.clients.planner_schemas import ActivationInput, DraftInput, FinalizeInput, ReviseInput, SignInput
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from shared.tenancy import tenant_context


def row(component, children=None, edition='test-1'):
    return {'source': {'component_id': component, 'edition': edition}, 'clinician_text': '', **(children or {})}


def payload():
    return {'title': 'Synthetic structured draft', 'plan_date': '2026-01-01', 'diagnosis_ids': [],
            'problems': [row('p1', {'goals': [row('g1', {'objectives': [row('o1', {'interventions': [row('i1')]})]})]})]}


@override_settings(DEBUG=True, PRACTICE_PLANNER_PROVIDER='mock')
class PlannerFinalizationTests(TestCase):
    def setUp(self):
        self.org, self.other_org = Organization.objects.bulk_create([
            Organization(name='Synthetic Finalize', slug='finalize-a', schema_name='finalize_a'),
            Organization(name='Synthetic Finalize Other', slug='finalize-b', schema_name='finalize_b'),
        ])
        self.ctx = tenant_context(self.org.pk); self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=901, wiley_enabled=True)
        self.admin = User.objects.create(email='synthetic-finalize@example.test', role='admin', external_admin_id=901)
        self.staff = User.objects.create(email='synthetic-finalize-staff@example.test', role='staff', external_admin_id=901)
        self.client_row = Client.objects.create(first_name='Synthetic', last_name='Finalize', external_admin_id=901)
        self.diagnosis = ClientDiagnosis.objects.create(client=self.client_row, code_system='SYNTHETIC', code_version='test',
            code='SYNTHETIC-001', display='Synthetic diagnosis wording')
        self.request = self.make_request(self.admin)

    def make_request(self, user):
        return SimpleNamespace(user=user, tenant=self.org,
            _jwt_payload={'sub': str(user.pk), 'type': 'access', 'org_id': self.org.pk})

    def create(self, values=None):
        return api.create_draft(self.request, self.client_row.pk, DraftInput(**(values or payload())))[1]

    def assert_http(self, status, call):
        with self.assertRaises(HttpError) as raised:
            call()
        self.assertEqual(raised.exception.status_code, status)

    def finalize(self, plan, version=None):
        return api.finalize_draft(self.request, self.client_row.pk, plan['id'], FinalizeInput(expected_lock_version=version or plan['lock_version']))

    def sign(self, plan_data, version, signature_data='Synthetic Signer', attested=True):
        return api.sign_draft(self.request, self.client_row.pk, plan_data['id'],
            SignInput(expected_lock_version=version, signature_data=signature_data, attested=attested))

    def test_finalize_requires_draft_with_a_problem_and_matching_lock_version(self):
        empty = self.create({**payload(), 'problems': []})
        self.assert_http(400, lambda: self.finalize(empty))
        plan = self.create()
        self.assert_http(409, lambda: self.finalize(plan, version=plan['lock_version'] + 1))
        finalized = self.finalize(plan)
        self.assertEqual(finalized['status'], 'finalized')
        self.assertEqual(finalized['lock_version'], plan['lock_version'] + 1)
        self.assert_http(409, lambda: self.finalize(finalized))

    def test_finalized_content_is_frozen_at_the_model_layer(self):
        plan = self.create()
        finalized = self.finalize(plan)
        row_model = TreatmentPlan.objects.get(pk=finalized['id'])
        row_model.title = 'Attempted bypass'
        with self.assertRaises(ValidationError):
            row_model.save()
        problem = row_model.problems.get()
        problem.clinician_text = 'Attempted structured-row bypass'
        with self.assertRaises(ValidationError):
            problem.save()

    def test_sign_requires_finalized_plan_role_and_attestation_and_binds_full_payload_hash(self):
        plan = self.create()
        self.assert_http(409, lambda: self.sign(plan, plan['lock_version']))
        finalized = self.finalize(plan)
        staff_request = self.make_request(self.staff)
        self.assert_http(403, lambda: api.sign_draft(staff_request, self.client_row.pk, finalized['id'],
            SignInput(expected_lock_version=finalized['lock_version'], signature_data='x', attested=True)))
        self.assert_http(400, lambda: self.sign(finalized, finalized['lock_version'], attested=False))
        signed = self.sign(finalized, finalized['lock_version'])
        signature = TreatmentPlanSignature.objects.get(plan_id=finalized['id'])
        self.assertTrue(signature.content_hash)
        self.assertEqual(len(signature.content_hash), 64)
        self.assertEqual(signed['status'], 'finalized')
        self.assert_http(409, lambda: self.sign(signed, signed['lock_version']))

    def test_signed_plan_rejects_further_structured_edits_and_admin_bulk_delete(self):
        from apps.clients.admin import TreatmentPlanAdmin
        plan = self.create()
        finalized = self.finalize(plan)
        self.sign(finalized, finalized['lock_version'])
        signed_plan = TreatmentPlan.objects.get(pk=finalized['id'])
        admin_instance = TreatmentPlanAdmin(TreatmentPlan, None)
        self.assertFalse(admin_instance.has_delete_permission(SimpleNamespace(), signed_plan))
        with self.assertRaises(ValidationError):
            signed_plan.status = TreatmentPlan.Status.DRAFT
            signed_plan.save()

    def test_revise_requires_finalized_plan_copies_tree_and_blocks_second_revision(self):
        data = payload(); data['diagnosis_ids'] = [self.diagnosis.pk]
        plan = self.create(data)
        self.assert_http(409, lambda: api.revise_draft(self.request, self.client_row.pk, plan['id'], ReviseInput(expected_lock_version=plan['lock_version'])))
        finalized = self.finalize(plan)
        revision = api.revise_draft(self.request, self.client_row.pk, finalized['id'], ReviseInput(expected_lock_version=finalized['lock_version']))[1]
        self.assertEqual(revision['status'], 'draft')
        self.assertEqual(revision['revision_number'], 2)
        self.assertEqual(revision['plan_series_id'], finalized['plan_series_id'])
        self.assertEqual(revision['diagnoses'][0]['diagnosis_id'], self.diagnosis.pk)
        intervention = revision['problems'][0]['goals'][0]['objectives'][0]['interventions'][0]
        self.assertIn('TEST ONLY', intervention['source']['text'])
        self.assertNotEqual(intervention['id'], plan['problems'][0]['goals'][0]['objectives'][0]['interventions'][0]['id'])
        self.assert_http(409, lambda: api.revise_draft(self.request, self.client_row.pk, finalized['id'], ReviseInput(expected_lock_version=finalized['lock_version'])))

    def test_activation_history_is_append_only_and_requires_finalization(self):
        plan = self.create()
        self.assert_http(409, lambda: api.activate_draft(self.request, self.client_row.pk, plan['id'],
            ActivationInput(expected_lock_version=plan['lock_version'], effective_from='2026-02-01')))
        finalized = self.finalize(plan)
        self.assert_http(400, lambda: api.activate_draft(self.request, self.client_row.pk, finalized['id'],
            ActivationInput(expected_lock_version=finalized['lock_version'], effective_from='2026-03-01', effective_to='2026-02-01')))
        first = api.activate_draft(self.request, self.client_row.pk, finalized['id'],
            ActivationInput(expected_lock_version=finalized['lock_version'], effective_from='2026-02-01', effective_to='2026-02-28'))[1]
        second = api.activate_draft(self.request, self.client_row.pk, finalized['id'],
            ActivationInput(expected_lock_version=finalized['lock_version'], effective_from='2026-03-01'))[1]
        history = api.list_activations(self.request, self.client_row.pk, finalized['id'])
        self.assertEqual([entry['id'] for entry in history], [first['id'], second['id']])
        activation = PlanActivation.objects.get(pk=first['id'])
        activation.effective_to = '2026-01-01'
        with self.assertRaises(Exception):
            activation.save()
