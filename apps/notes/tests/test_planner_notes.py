"""Stage 5: progress-note autofill preview/apply, addenda and homework."""
from datetime import date
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.clients import planner_api as plan_api
from apps.clients.models import Client, ClientDiagnosis, TreatmentPlan
from apps.clients.planner_schemas import ActivationInput, DraftInput, FinalizeInput
from apps.integrations.models import PlannerContentSnapshot
from apps.notes import planner_api as api
from apps.notes.models import ClientHomeworkAssignment, LessonNote, NoteSignature
from apps.notes.planner_schemas import AddendumInput, ApplyInput, HomeworkAssignInput, ObjectiveSelection
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from shared.tenancy import tenant_context


def row(component, children=None, edition='test-1'):
    return {'source': {'component_id': component, 'edition': edition}, 'clinician_text': '', **(children or {})}


def payload():
    return {'title': 'Synthetic structured draft', 'plan_date': '2026-01-01', 'diagnosis_ids': [],
            'problems': [row('p1', {'goals': [row('g1', {'objectives': [row('o1', {'interventions': [row('i1')]})]})]})]}


@override_settings(DEBUG=True, PRACTICE_PLANNER_PROVIDER='mock')
class PlannerNotesTests(TestCase):
    def setUp(self):
        self.org, self.other_org = Organization.objects.bulk_create([
            Organization(name='Synthetic Notes', slug='notes-a', schema_name='notes_a'),
            Organization(name='Synthetic Notes Other', slug='notes-b', schema_name='notes_b'),
        ])
        self.ctx = tenant_context(self.org.pk); self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=901, wiley_enabled=True)
        self.admin = User.objects.create(email='synthetic-notes-admin@example.test', role='admin', external_admin_id=901)
        self.staff = User.objects.create(email='synthetic-notes-staff@example.test', role='staff', external_admin_id=901)
        self.client_row = Client.objects.create(first_name='Synthetic', last_name='Notes', external_admin_id=901)
        self.request = self.make_request(self.admin)

        plan_request = self.request
        plan = plan_api.create_draft(plan_request, self.client_row.pk, DraftInput(**payload()))[1]
        finalized = plan_api.finalize_draft(plan_request, self.client_row.pk, plan['id'], FinalizeInput(expected_lock_version=plan['lock_version']))
        plan_api.activate_draft(plan_request, self.client_row.pk, finalized['id'],
            ActivationInput(expected_lock_version=finalized['lock_version'], effective_from='2026-01-01'))
        self.plan = TreatmentPlan.objects.get(pk=finalized['id'])
        self.objective_id = self.plan.problems.get().goals.get().objectives.get().pk

        self.note = LessonNote.objects.create(
            staff=self.admin, external_client_id=self.client_row.pk, note_date=date(2026, 2, 1), body={},
        )

    def make_request(self, user):
        return SimpleNamespace(user=user, tenant=self.org,
            _jwt_payload={'sub': str(user.pk), 'type': 'access', 'org_id': self.org.pk})

    def assert_http(self, status, call):
        with self.assertRaises(HttpError) as raised:
            call()
        self.assertEqual(raised.exception.status_code, status)

    def test_active_plan_resolves_only_within_activation_window(self):
        resolved = api.active_plan(self.request, self.note.pk)
        self.assertEqual(resolved['plan_id'], self.plan.pk)
        self.assertEqual(resolved['problems'][0]['goals'][0]['objectives'][0]['id'], self.objective_id)
        early_note = LessonNote.objects.create(staff=self.admin, external_client_id=self.client_row.pk, note_date=date(2025, 12, 1), body={})
        self.assert_http(404, lambda: api.active_plan(self.request, early_note.pk))

    def test_preview_never_claims_occurrence_and_apply_fills_once_then_preserves_narrative(self):
        preview = api.planner_preview(self.request, self.note.pk, ObjectiveSelection(objective_ids=[self.objective_id]))
        self.assertIn('Objective addressed', preview['rendered_content'])
        for banned in ('completed', 'progress was made', 'improved'):
            self.assertNotIn(banned, preview['rendered_content'].lower())

        applied = api.planner_apply(self.request, self.note.pk, ApplyInput(objective_ids=[self.objective_id], field_key='session_summary'))
        self.assertTrue(applied['applied'])
        self.note.refresh_from_db()
        self.assertEqual(self.note.body['session_summary'], applied['rendered_content'])

        self.note.body['session_summary'] = 'Clinician already wrote something here'
        self.note.save(update_fields=['body'])
        skipped = api.planner_apply(self.request, self.note.pk, ApplyInput(objective_ids=[self.objective_id], field_key='session_summary'))
        self.assertFalse(skipped['applied'])
        self.note.refresh_from_db()
        self.assertEqual(self.note.body['session_summary'], 'Clinician already wrote something here')

        replaced = api.planner_apply(self.request, self.note.pk,
            ApplyInput(objective_ids=[self.objective_id], field_key='session_summary', mode='replace'))
        self.assertTrue(replaced['applied'])

        records = api.planner_applications(self.request, self.note.pk)
        self.assertEqual(len(records), 3)
        self.assertEqual([r['applied'] for r in records], [True, False, True])

    def test_objective_must_belong_to_the_resolved_plan(self):
        self.assert_http(400, lambda: api.planner_preview(self.request, self.note.pk, ObjectiveSelection(objective_ids=[999999])))

    def test_applications_are_append_only(self):
        api.planner_apply(self.request, self.note.pk, ApplyInput(objective_ids=[self.objective_id], field_key='session_summary'))
        record = self.note.planner_applications.get()
        record.rendered_content = 'Attempted bypass'
        with self.assertRaises(ValidationError):
            record.save()

    def test_signature_binds_a_content_hash_that_changes_with_applied_provenance(self):
        from apps.notes.services import submit_note, approve_note
        api.planner_apply(self.request, self.note.pk, ApplyInput(objective_ids=[self.objective_id], field_key='session_summary'))
        self.note.refresh_from_db()
        submit_note(self.note, self.admin)
        approve_note(self.note, self.admin)
        from apps.notes import api as notes_api
        from apps.notes.schemas import SignNoteRequest
        request = SimpleNamespace(user=self.admin, META={})
        _, signature = notes_api.sign_note(request, self.note.pk, SignNoteRequest(signature_type='supervisor', signature_data='Synthetic'))
        self.assertEqual(len(signature.content_hash), 64)
        other_hash = NoteSignature.objects.get(pk=signature.pk)
        other_hash.content_hash = 'tampered' * 8
        with self.assertRaises(Exception):
            other_hash.save()

    def test_addendum_requires_a_signed_note_and_is_append_only(self):
        self.assert_http(409, lambda: api.create_addendum(self.request, self.note.pk, AddendumInput(text='Too early')))
        NoteSignature.objects.create(note=self.note, signer_id=self.admin.id, signer_name='Synthetic', signer_role='admin',
            signature_type='supervisor', content_hash='a' * 64)
        created = api.create_addendum(self.request, self.note.pk, AddendumInput(text='Correction after signing'))
        self.assertEqual(created[1]['text'], 'Correction after signing')
        listed = api.addenda(self.request, self.note.pk)
        self.assertEqual(len(listed), 1)
        from apps.notes.models import NoteAddendum
        addendum = NoteAddendum.objects.get(pk=created[1]['id'])
        addendum.text = 'Attempted bypass'
        with self.assertRaises(ValidationError):
            addendum.save()

    def test_homework_assignment_requires_homework_category_and_is_append_only(self):
        self.assert_http(400, lambda: api.create_homework(self.request, self.note.pk,
            HomeworkAssignInput(problem_component_id='p1', homework_component_id='i1', edition='test-1')))
        _, assignment = api.create_homework(self.request, self.note.pk,
            HomeworkAssignInput(problem_component_id='p1', homework_component_id='h1', edition='test-1'))
        self.assertEqual(assignment['status'], 'assigned')
        self.assertEqual(PlannerContentSnapshot.objects.filter(component_id='h1').count(), 1)
        completed = api.complete_homework_assignment(self.request, assignment['id'])
        self.assertEqual(completed['status'], 'completed')
        self.assert_http(409, lambda: api.complete_homework_assignment(self.request, assignment['id']))
        listed = api.note_homework(self.request, self.note.pk)
        self.assertEqual(listed[0]['id'], assignment['id'])
        row_model = ClientHomeworkAssignment.objects.get(pk=assignment['id'])
        row_model.client = Client.objects.create(first_name='Other', last_name='Client', external_admin_id=901)
        with self.assertRaises(ValidationError):
            row_model.save()

    def test_staff_cannot_access_another_staffs_note(self):
        staff_request = self.make_request(self.staff)
        self.assert_http(403, lambda: api.active_plan(staff_request, self.note.pk))

    def test_note_with_no_client_has_no_active_plan(self):
        orphan = LessonNote.objects.create(staff=self.admin, external_client_id=None, note_date=date(2026, 2, 1), body={})
        self.assert_http(404, lambda: api.active_plan(self.request, orphan.pk))
