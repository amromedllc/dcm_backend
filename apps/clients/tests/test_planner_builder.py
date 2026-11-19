"""Structured draft API regressions with synthetic data and no external calls."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from ninja.errors import HttpError
from ninja.testing import TestClient

from apps.accounts.auth import create_access_token
from apps.accounts.models import User, RolePermission
from apps.clients import planner_api as api
from apps.clients.models import Client, ClientStaffAssignment, ClientDiagnosis, TreatmentPlan
from apps.clients.planner_schemas import DraftInput, DraftUpdate, DiagnosisInput, DiagnosisUpdate, DraftDelete, LookupInput, ChildrenInput
from apps.integrations.models import PlannerContentSnapshot
from apps.integrations.practice_planner.contracts import PlannerUnavailable
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from shared.tenancy import tenant_context


def row(component, children=None, edition='test-1'):
    return {'source': {'component_id': component, 'edition': edition}, 'clinician_text': '', **(children or {})}


def payload():
    return {'title': 'Synthetic structured draft', 'plan_date': '2026-01-01', 'diagnosis_ids': [],
            'problems': [row('p1', {'goals': [row('g1', {'objectives': [row('o1', {'interventions': [row('i1')]})]})]})]}


def edit_payload(plan):
    def input_row(value):
        result = {'id': value['id'], 'source': {key: value['source'][key] for key in ('component_id', 'edition', 'language')},
                  'clinician_text': value['clinician_text']}
        for key in ('goals', 'objectives', 'interventions'):
            if key in value:
                result[key] = [input_row(child) for child in value[key]]
        return result
    return {'title': plan['title'], 'plan_date': plan['plan_date'], 'expected_lock_version': plan['lock_version'],
            'date_from': plan['date_from'], 'date_to': plan['date_to'],
            'diagnosis_ids': [d['diagnosis_id'] for d in plan['diagnoses']], 'problems': [input_row(r) for r in plan['problems']]}


@override_settings(DEBUG=True, PRACTICE_PLANNER_PROVIDER='mock')
class PlannerBuilderTests(TestCase):
    def setUp(self):
        self.org, self.other_org = Organization.objects.bulk_create([
            Organization(name='Synthetic Builder', slug='builder-a', schema_name='builder_a'),
            Organization(name='Synthetic Other', slug='builder-b', schema_name='builder_b'),
        ])
        self.ctx = tenant_context(self.org.pk); self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.facility = OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=901, wiley_enabled=True)
        self.admin = User.objects.create(email='synthetic-builder@example.test', role='admin', external_admin_id=901)
        self.staff = User.objects.create(email='synthetic-builder-staff@example.test', role='staff', external_admin_id=901)
        self.client_row = Client.objects.create(first_name='Synthetic', last_name='Builder', external_admin_id=901)
        self.unassigned = Client.objects.create(first_name='Synthetic', last_name='Unassigned', external_admin_id=901)
        self.foreign_facility = Client.objects.create(first_name='Synthetic', last_name='Other Facility', external_admin_id=902)
        self.diagnosis = ClientDiagnosis.objects.create(client=self.client_row, code_system='SYNTHETIC', code_version='test',
            code='SYNTHETIC-001', display='Synthetic diagnosis wording')
        self.request = self.make_request(self.admin)

    def make_request(self, user):
        return SimpleNamespace(user=user, tenant=self.org,
            _jwt_payload={'sub': str(user.pk), 'type': 'access', 'org_id': self.org.pk})

    def create(self, values=None, request=None, client_id=None):
        return api.create_draft(request or self.request, client_id or self.client_row.pk, DraftInput(**(values or payload())))[1]

    def save(self, plan, values=None):
        return api.update_draft(self.request, self.client_row.pk, plan['id'], DraftUpdate(**(values or edit_payload(plan))))

    def assert_http(self, status, call):
        with self.assertRaises(HttpError) as raised:
            call()
        self.assertEqual(raised.exception.status_code, status)

    def test_complete_builder_flow_and_preserved_source_wording(self):
        data = payload(); data['diagnosis_ids'] = [self.diagnosis.pk]
        plan = self.create(data)
        changed = edit_payload(plan)
        changed['problems'][0]['goals'][0]['objectives'][0]['interventions'][0]['clinician_text'] = 'Original synthetic clinician wording'
        updated = self.save(plan, changed)
        self.assertEqual(updated['lock_version'], 2)
        self.assertEqual(updated['problems'][0]['id'], plan['problems'][0]['id'])
        intervention = updated['problems'][0]['goals'][0]['objectives'][0]['interventions'][0]
        self.assertEqual(intervention['clinician_text'], 'Original synthetic clinician wording')
        self.assertIn('TEST ONLY', intervention['source']['text'])
        self.assertEqual(api.read_draft(self.request, self.client_row.pk, plan['id'])['problems'], updated['problems'])
        self.assertEqual(len(api.list_drafts(self.request, self.client_row.pk)), 1)

    def test_stale_editor_cannot_overwrite_or_delete_and_no_snapshots_added(self):
        plan = self.create()
        self.save(plan)
        count = PlannerContentSnapshot.objects.count()
        self.assert_http(409, lambda: self.save(plan))
        self.assert_http(409, lambda: api.delete_draft(self.request, self.client_row.pk, plan['id'], DraftDelete(expected_lock_version=1)))
        self.assertEqual(PlannerContentSnapshot.objects.count(), count)
        self.assertEqual(TreatmentPlan.objects.get(pk=plan['id']).lock_version, 2)

    def test_invalid_hierarchy_rolls_back_plan_rows_sources_and_audit(self):
        data = payload()
        data['problems'][0]['goals'][0]['source']['component_id'] = 'i1'
        with patch('apps.integrations.practice_planner.audit.log_event') as audit:
            with self.captureOnCommitCallbacks(execute=True):
                self.assert_http(400, lambda: self.create(data))
            audit.assert_not_called()
        self.assertFalse(TreatmentPlan.objects.exists())
        self.assertFalse(PlannerContentSnapshot.objects.exists())

    def test_invalid_save_rolls_back_title_version_and_deletions(self):
        plan = self.create()
        changes = edit_payload(plan); changes['title'] = 'Should roll back'
        changes['problems'].append(row('g1'))
        self.assert_http(400, lambda: self.save(plan, changes))
        saved = api.read_draft(self.request, self.client_row.pk, plan['id'])
        self.assertEqual(saved['title'], plan['title'])
        self.assertEqual(saved['lock_version'], 1)
        self.assertEqual(saved['problems'], plan['problems'])

    def test_wrong_parent_other_plan_and_duplicate_row_ids_rejected(self):
        plan = self.create(); other = self.create()
        for rows in ([edit_payload(other)['problems'][0]], [edit_payload(plan)['problems'][0]] * 2):
            changes = edit_payload(plan); changes['problems'] = rows
            self.assert_http(400, lambda: self.save(plan, changes))
        changes = edit_payload(plan)
        changes['problems'].append(row('p2', {'goals': deepcopy(changes['problems'][0]['goals'])}))
        self.assert_http(400, lambda: self.save(plan, changes))

    def test_existing_source_cannot_be_swapped_and_editions_must_match(self):
        plan = self.create(); changes = edit_payload(plan)
        changes['problems'][0]['source']['component_id'] = 'p2'
        self.assert_http(400, lambda: self.save(plan, changes))
        data = payload(); data['problems'][0]['goals'][0]['source']['edition'] = 'test-2'
        self.assert_http(400, lambda: self.create(data))
        data = payload(); data['problems'][0]['goals'][0]['objectives'][0]['interventions'] = [row('i2', edition='test-2')]
        self.assert_http(400, lambda: self.create(data))
        data = payload()
        current = data['problems'][0]
        for key in ('goals', 'objectives', 'interventions'):
            current['source']['edition'] = 'test-2'
            current = current[key][0]
        current['source'] = {'component_id': 'i2', 'edition': 'test-2'}
        self.assert_http(400, lambda: self.create(data))

    def test_diagnoses_must_belong_to_client_and_be_active_on_first_selection(self):
        other_dx = ClientDiagnosis.objects.create(client=self.unassigned, code_system='SYNTHETIC', code_version='test', code='TEST', display='Other synthetic diagnosis')
        for ids in ([other_dx.pk], [self.diagnosis.pk, self.diagnosis.pk], [999]):
            data = payload(); data['diagnosis_ids'] = ids
            self.assert_http(400, lambda: self.create(data))
        self.diagnosis.clinical_status = 'resolved'; self.diagnosis.save()
        data = payload(); data['diagnosis_ids'] = [self.diagnosis.pk]
        self.assert_http(400, lambda: self.create(data))

    def test_selected_diagnosis_snapshot_survives_later_diagnosis_changes(self):
        data = payload(); data['diagnosis_ids'] = [self.diagnosis.pk]
        plan = self.create(data)
        self.diagnosis.display = 'New synthetic wording'; self.diagnosis.clinical_status = 'resolved'; self.diagnosis.save()
        updated = self.save(plan)
        self.assertEqual(updated['diagnoses'][0]['display'], 'Synthetic diagnosis wording')

    def test_pruning_rows_keeps_retained_ids_and_immutable_source_snapshots(self):
        plan = self.create(); changes = edit_payload(plan)
        changes['problems'][0]['goals'] = []
        updated = self.save(plan, changes)
        self.assertEqual(updated['problems'][0]['id'], plan['problems'][0]['id'])
        self.assertEqual(updated['problems'][0]['goals'], [])
        self.assertEqual(PlannerContentSnapshot.objects.count(), 4)

    def test_off_switch_blocks_authoring_but_saved_reads_stay_available(self):
        plan = self.create()
        self.facility.wiley_enabled = False; self.facility.save()
        self.assertEqual(api.read_draft(self.request, self.client_row.pk, plan['id'])['id'], plan['id'])
        self.assertEqual(len(api.list_diagnoses(self.request, self.client_row.pk)), 1)
        self.assert_http(403, lambda: self.create())
        self.assert_http(403, lambda: self.save(plan))
        self.assert_http(403, lambda: api.delete_draft(self.request, self.client_row.pk, plan['id'], DraftDelete(expected_lock_version=1)))

    def test_staff_permission_assignment_and_facility_boundaries(self):
        req = self.make_request(self.staff)
        self.assert_http(403, lambda: self.create(request=req))
        RolePermission.objects.create(organization=self.org, role='staff', permissions={'client_treatment_plan': True})
        self.assert_http(404, lambda: self.create(request=req))
        ClientStaffAssignment.objects.create(client=self.client_row, user=self.staff)
        self.assertTrue(self.create(request=req)['id'])
        self.assert_http(404, lambda: self.create(request=req, client_id=self.unassigned.pk))
        self.assert_http(404, lambda: self.create(client_id=self.foreign_facility.pk))
        ClientStaffAssignment.objects.filter(user=self.staff).update(is_active=False)
        self.assert_http(404, lambda: api.list_drafts(req, self.client_row.pk))

    def test_wrong_tenant_and_disallowed_role_cannot_read_saved_drafts(self):
        plan = self.create()
        with tenant_context(self.other_org.pk):
            self.assert_http(403, lambda: api.read_draft(self.request, self.client_row.pk, plan['id']))
        User.objects.filter(pk=self.admin.pk).update(role='reporting')
        self.assert_http(403, lambda: api.list_drafts(self.request, self.client_row.pk))

    def test_diagnosis_creation_status_concurrency_and_cross_client_supersession(self):
        data = DiagnosisInput(code_system='ICD-10-CM', code_version='test', code='TEST-001', display='Synthetic ICD fixture')
        _, diagnosis = api.create_diagnosis(self.request, self.client_row.pk, data)
        updated = api.update_diagnosis(self.request, self.client_row.pk, diagnosis['id'], DiagnosisUpdate(expected_lock_version=1, clinical_status='resolved'))
        self.assertEqual(updated['lock_version'], 2)
        self.assert_http(409, lambda: api.update_diagnosis(self.request, self.client_row.pk, diagnosis['id'], DiagnosisUpdate(expected_lock_version=1, clinical_status='active')))
        self.assert_http(400, lambda: api.create_diagnosis(self.request, self.unassigned.pk,
            DiagnosisInput(**{**data.model_dump(), 'supersedes_id': diagnosis['id']})))

    def test_diagnosis_mutations_need_clients_edit_permission(self):
        req = self.make_request(self.staff)
        RolePermission.objects.create(organization=self.org, role='staff', permissions={'client_treatment_plan': True, 'clients_edit': False})
        ClientStaffAssignment.objects.create(client=self.client_row, user=self.staff)
        self.assert_http(403, lambda: api.create_diagnosis(req, self.client_row.pk,
            DiagnosisInput(code_system='SYNTHETIC', code_version='test', code='TEST', display='Synthetic')))

    def test_lookup_paginated_and_no_match_are_explicit(self):
        query = LookupInput(system='SYNTHETIC', version='test', code='SYNTHETIC-001', edition='test-1')
        first = api.lookup_content(self.request, self.client_row.pk, query)
        second = api.lookup_content(self.request, self.client_row.pk, query.model_copy(update={'cursor': first['next_cursor']}))
        self.assertEqual(first['items'][0]['component_id'], 'p1')
        self.assertEqual(second['items'][0]['component_id'], 'p2')
        empty = api.lookup_content(self.request, self.client_row.pk, query.model_copy(update={'system': 'DSM-5'}))
        self.assertEqual(empty['items'], ())
        children = api.content_children(self.request, self.client_row.pk, ChildrenInput(component_id='o1', edition='test-1'))
        self.assertEqual(children['items'][0]['category'], 'intervention')

    def test_provider_failure_returns_sanitized_error(self):
        with patch('apps.clients.planner_api.service_for_user', side_effect=PlannerUnavailable('Synthetic private detail')):
            self.assert_http(503, lambda: self.create())

    def test_live_mode_still_requires_facility_entitlement(self):
        # Stage 6: live mode is no longer blanket-blocked, but an unentitled
        # facility (the default in this test's setUp) must still be denied.
        with override_settings(PRACTICE_PLANNER_PROVIDER='live', WILEY_API_KEY='synthetic-test-key'):
            self.assert_http(403, lambda: self.create())
        self.facility.wiley_entitled = True
        self.facility.save()
        with override_settings(PRACTICE_PLANNER_PROVIDER='live', WILEY_API_KEY='synthetic-test-key'):
            with patch('apps.integrations.practice_planner.live_provider.fetch_json',
                       side_effect=Exception('must not call the live provider from this test')):
                self.assert_http(400, lambda: self.create())

    def test_legacy_endpoints_cannot_mutate_or_sign_structured_plans(self):
        from apps.clients import api as legacy
        from apps.clients.schemas import TreatmentPlanUpdateRequest, SignTreatmentPlanRequest
        plan = self.create()
        self.assertEqual(legacy.list_treatment_plans(self.request, self.client_row.pk), [])
        self.assert_http(404, lambda: legacy.update_treatment_plan(self.request, self.client_row.pk, plan['id'], TreatmentPlanUpdateRequest(title='Bypass')))
        self.assert_http(404, lambda: legacy.delete_treatment_plan(self.request, self.client_row.pk, plan['id']))
        self.assert_http(404, lambda: legacy.sign_treatment_plan(self.request, self.client_row.pk, plan['id'], SignTreatmentPlanRequest(signature_data='Synthetic', attested=True)))

    def test_request_validation_rejects_forged_text_status_and_excessive_rows(self):
        http = TestClient(api.router)
        headers = {'Authorization': f'Bearer {create_access_token(self.admin, self.org.pk)}'}
        with patch('apps.accounts.auth.is_token_blocked', return_value=False):
            for mutate in (
                lambda data: data.update(status='finalized'),
                lambda data: data['problems'][0]['source'].update(text='Forged source text'),
                lambda data: data.update(problems=[row('p1')] * 21),
            ):
                data = payload(); mutate(data)
                self.assertEqual(http.post(f'/clients/{self.client_row.pk}/plans', json=data, headers=headers, tenant=self.org).status_code, 422)
            response = http.post(f'/clients/{self.client_row.pk}/plans', json=payload(), headers=headers, tenant=self.org)
            self.assertEqual(response.status_code, 201)
        self.assertEqual(http.post(f'/clients/{self.client_row.pk}/plans', json=payload(), tenant=self.org).status_code, 401)

    def test_total_row_limit_invalid_dates_and_empty_draft(self):
        data = payload(); data['date_from'] = '2026-02-01'; data['date_to'] = '2026-01-01'
        self.assert_http(400, lambda: self.create(data))
        data = payload(); data['problems'] = [row('p1', {'goals': [row('g1', {'objectives': [row('o1')] * 40})] * 10})]
        self.assert_http(400, lambda: self.create(data))
        data = payload(); data['problems'] = []
        plan = self.create(data)
        self.assertEqual(plan['problems'], [])
        self.assertEqual(api.delete_draft(self.request, self.client_row.pk, plan['id'], DraftDelete(expected_lock_version=1)), (204, None))
