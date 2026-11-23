"""Reusable selection with synthetic records in a disposable test database."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError
from apps.accounts.models import User, RolePermission
from apps.clients.models import Client, ClientStaffAssignment
from apps.programs.models import Lesson, Program, Target, TargetStatus, WorkflowTemplate
from apps.programs.api import create_lesson, update_lesson, delete_lesson, list_lessons
from apps.programs.schemas import LessonCreateRequest, LessonUpdateRequest
from apps.sessions.api import start_session, assign_appointment_programs
from apps.sessions.schemas import SessionStartRequest, AssignProgramsRequest
from apps.sessions.models import Appointment, SessionRun, TrialEvent
from apps.sessions.services import build_program_snapshot, resolve_session_lesson, _target_due_for_session
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from shared.tenancy import tenant_context


class SessionSetupTests(TestCase):
    def setUp(self):
        self.org = Organization(name='Synthetic clinic', slug='setups', schema_name='setups')
        self.other_org = Organization(name='Other clinic', slug='other-setups', schema_name='other_setups')
        Organization.objects.bulk_create([self.org, self.other_org])
        context = tenant_context(self.org.pk)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=101)
        self.admin = User.objects.create_user('setup-admin@example.test', role='admin', organization=self.org, external_admin_id=101)
        self.staff = User.objects.create_user('setup-staff@example.test', role='staff', organization=self.org, external_admin_id=101)
        self.client_row = Client.objects.create(first_name='Synthetic', last_name='Client', external_id='501', external_admin_id=101)
        self.other_client = Client.objects.create(first_name='Other', last_name='Client', external_id='502', external_admin_id=101)
        ClientStaffAssignment.objects.create(client=self.client_row, user=self.staff)
        self.request = SimpleNamespace(user=self.admin, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        self.staff_request = SimpleNamespace(user=self.staff, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        for key in ['probe', 'acquisition', 'mastered']:
            TargetStatus.objects.create(key=key, label=key.title(), is_staff_visible=True)
        TargetStatus.objects.create(key='closed', label='Closed', is_staff_visible=False)
        self.programs = [Program.objects.create(name=f'Synthetic program {i}', external_client_id=self.client_row.id,
                         category='skill_acquisition', created_by=self.admin) for i in range(3)]
        self.targets = [Target.objects.create(program=p, name='Synthetic target', status='probe') for p in self.programs]
        self.appt = Appointment.objects.create(external_client_id=501, staff=self.staff, start_time=timezone.now(), end_time=timezone.now() + timedelta(hours=1))

    def setup_for(self, programs=None, **extra):
        _, data = create_lesson(self.request, LessonCreateRequest(client_id=self.client_row.id, name='Daily session',
                  program_ids=[p.id for p in (programs if programs is not None else self.programs)], **extra))
        return Lesson.objects.get(pk=data['id'])

    def snapshot(self, setup):
        return build_program_snapshot(self.client_row.id, setup.id)

    def test_first_setup_default_and_list_hides_appointment_lists(self):
        setup = self.setup_for()
        Lesson.objects.create(name='Appointment list', external_client_id=self.client_row.id)
        self.assertTrue(setup.is_default)
        self.assertEqual([row['id'] for row in list_lessons(self.request, self.client_row.id)], [setup.id])

    def test_default_reused_for_future_appointments_and_quick_start(self):
        setup = self.setup_for()
        self.assertEqual(resolve_session_lesson(self.client_row.id).id, setup.id)
        self.assertEqual(resolve_session_lesson(self.client_row.id, appointment=self.appt).id, setup.id)
        self.assertEqual(resolve_session_lesson(501, appointment=self.appt).id, setup.id)
        self.appt.refresh_from_db()
        self.assertIsNone(self.appt.lesson_id)

    def test_explicit_and_appointment_selections_take_precedence(self):
        self.setup_for()
        override = self.setup_for([self.programs[1]])
        explicit = self.setup_for([self.programs[2]])
        self.appt.lesson = override
        self.appt.save()
        self.assertEqual(resolve_session_lesson(self.client_row.id, appointment=self.appt).id, override.id)
        self.assertEqual(resolve_session_lesson(self.client_row.id, explicit.id, self.appt).id, explicit.id)

    def test_switch_default_and_archive_preserve_recordings(self):
        first = self.setup_for()
        second = self.setup_for([self.programs[1]])
        run = SessionRun.objects.create(external_client_id=501, staff=self.admin, lesson=first, program_snapshot=self.snapshot(first))
        update_lesson(self.request, second.id, LessonUpdateRequest(is_default=True))
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertEqual(resolve_session_lesson(self.client_row.id).id, second.id)
        delete_lesson(self.request, second.id)
        self.assertIsNone(resolve_session_lesson(self.client_row.id))
        run.refresh_from_db()
        self.assertTrue(run.program_snapshot['programs'])

    def test_database_rejects_duplicate_or_inactive_default(self):
        first = self.setup_for()
        second = self.setup_for([self.programs[1]])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Lesson.objects.filter(pk=second.pk).update(is_default=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Lesson.objects.filter(pk=first.pk).update(is_active=False)

    def test_status_updates_next_run_without_reassignment_or_history_change(self):
        setup = self.setup_for()
        run = SessionRun.objects.create(external_client_id=501, staff=self.admin, lesson=setup, program_snapshot=self.snapshot(setup))
        self.targets[0].status = 'acquisition'
        self.targets[0].save()
        self.assertEqual(self.snapshot(setup)['programs'][0]['targets'][0]['status'], 'acquisition')
        run.refresh_from_db()
        self.assertEqual(run.program_snapshot['programs'][0]['targets'][0]['status'], 'probe')
        self.assertEqual(setup.lesson_programs.count(), 3)

    def test_unrecorded_programs_remain_but_closed_program_is_omitted(self):
        setup = self.setup_for()
        self.programs[0].phase = 'closed'
        self.programs[0].save()
        self.assertEqual([p['id'] for p in self.snapshot(setup)['programs']], [p.id for p in self.programs[1:]])
        self.assertEqual(setup.lesson_programs.count(), 3)

    def test_inactive_program_not_recorded(self):
        setup = self.setup_for()
        self.programs[0].status = 'inactive'
        self.programs[0].save()
        self.assertNotIn(self.programs[0].id, [p['id'] for p in self.snapshot(setup)['programs']])

    def test_mastered_without_maintenance_omitted(self):
        setup = self.setup_for()
        self.targets[0].status = 'mastered'
        self.targets[0].save()
        self.assertFalse(self.snapshot(setup)['programs'][0]['targets'])

    def test_mastered_initial_maintenance_check_due(self):
        workflow = WorkflowTemplate.objects.create(name='Maintenance', phases=[{'phase': 'mastered', 'maintenance': {'intervals': [{'type': 'weekly', 'value': 1}]}}])
        target = self.targets[0]
        target.status = 'mastered'
        target.workflow_template = workflow
        target.save()
        self.assertTrue(_target_due_for_session(target))

    def test_pins_and_order(self):
        setup = self.setup_for(pinned_program_ids=[self.programs[2].id])
        self.assertEqual([p['id'] for p in self.snapshot(setup)['programs']], [self.programs[2].id, self.programs[0].id, self.programs[1].id])
        update_lesson(self.request, setup.id, LessonUpdateRequest(program_ids=[self.programs[1].id, self.programs[0].id], pinned_program_ids=[]))
        self.assertEqual([p['id'] for p in self.snapshot(setup)['programs']], [self.programs[1].id, self.programs[0].id])

    def test_invalid_pin_rejected_without_partial_setup(self):
        with self.assertRaises(HttpError) as error:
            self.setup_for([self.programs[0]], pinned_program_ids=[self.programs[1].id])
        self.assertEqual(error.exception.status_code, 400)
        self.assertFalse(Lesson.objects.exists())

    def test_cross_client_program_selection_and_setup_start_rejected(self):
        foreign = Program.objects.create(name='Other program', category='skill_acquisition', external_client_id=self.other_client.id)
        with self.assertRaises(HttpError):
            self.setup_for([foreign])
        setup = self.setup_for()
        with self.assertRaises(HttpError):
            resolve_session_lesson(self.other_client.id, setup.id)
        with self.assertRaises(HttpError):
            build_program_snapshot(self.other_client.id, setup.id)

    def test_staff_cannot_manage_or_read_unassigned_client(self):
        with self.assertRaises(HttpError) as error:
            create_lesson(self.staff_request, LessonCreateRequest(client_id=self.client_row.id, name='Not permitted', program_ids=[self.programs[0].id]))
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HttpError):
            list_lessons(self.staff_request, self.other_client.id)

    def test_other_organization_setup_is_inaccessible(self):
        setup = self.setup_for()
        with tenant_context(self.other_org.pk):
            with self.assertRaises(HttpError):
                resolve_session_lesson(self.client_row.id, setup.id)
            self.assertFalse(Lesson.objects.exists())

    def test_start_uses_default_and_does_not_mutate_appointment(self):
        setup = self.setup_for()
        status, result = start_session(self.staff_request, SessionStartRequest(client_id=self.client_row.id, appointment_id=self.appt.id))
        self.assertEqual(status, 201)
        self.assertEqual(result['lesson_id'], setup.id)
        self.assertEqual(len(result['program_snapshot']['programs']), 3)
        self.appt.refresh_from_db()
        self.assertIsNone(self.appt.lesson_id)

    def test_unassigned_appointment_fallback_matches_warning(self):
        _, result = start_session(self.staff_request, SessionStartRequest(client_id=self.client_row.id, appointment_id=self.appt.id))
        self.assertEqual(len(result['program_snapshot']['programs']), 3)

    def test_foreign_client_appointment_rejected(self):
        with self.assertRaises(HttpError):
            start_session(self.request, SessionStartRequest(client_id=self.other_client.id, appointment_id=self.appt.id))
        self.assertFalse(SessionRun.objects.exists())

    def test_appointment_override_edit_and_clear_preserve_shared_setup(self):
        setup = self.setup_for()
        self.appt.lesson = setup
        self.appt.save()
        assign_appointment_programs(self.request, self.appt.id, AssignProgramsRequest(program_ids=[self.programs[1].id]))
        self.appt.refresh_from_db()
        self.assertNotEqual(self.appt.lesson_id, setup.id)
        self.assertEqual(setup.lesson_programs.count(), 3)
        assign_appointment_programs(self.request, self.appt.id, AssignProgramsRequest(program_ids=[], clear_assignment=True))
        setup.refresh_from_db()
        self.assertTrue(setup.is_default)
        self.assertEqual(resolve_session_lesson(self.client_row.id, appointment=Appointment.objects.get(pk=self.appt.id)).id, setup.id)

    def test_private_appointment_list_cannot_be_guessed_as_reusable_setup(self):
        private = Lesson.objects.create(name='Private appointment list', external_client_id=self.client_row.id)
        with self.assertRaises(HttpError):
            resolve_session_lesson(self.client_row.id, private.id)

    def test_staff_cannot_start_another_providers_appointment(self):
        self.appt.staff = self.admin
        self.appt.save()
        with self.assertRaises(HttpError):
            start_session(self.staff_request, SessionStartRequest(client_id=self.client_row.id, appointment_id=self.appt.id))
        self.assertFalse(SessionRun.objects.exists())

    def test_read_permission_and_write_permission_are_enforced(self):
        RolePermission.objects.create(organization=self.org, role='staff', permissions={'client_sessions': False, 'session_start': False})
        with self.assertRaises(HttpError) as error:
            list_lessons(self.staff_request, self.client_row.id)
        self.assertEqual(error.exception.status_code, 403)
        supervisor = User.objects.create_user('setup-supervisor@example.test', role='supervisor', organization=self.org, external_admin_id=101)
        RolePermission.objects.create(organization=self.org, role='supervisor', permissions={'sessions_edit': False})
        request = SimpleNamespace(user=supervisor, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        with self.assertRaises(HttpError) as error:
            create_lesson(request, LessonCreateRequest(client_id=self.client_row.id, name='Not permitted', program_ids=[self.programs[0].id]))
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(Lesson.objects.exists())

    def test_therapist_instructions_are_copied_into_new_recording(self):
        self.setup_for(therapist_message='Synthetic instruction: prepare materials.')
        _, result = start_session(self.staff_request, SessionStartRequest(client_id=self.client_row.id))
        self.assertEqual(result['session_name'], 'Daily session')
        self.assertEqual(result['message_to_therapist'], 'Synthetic instruction: prepare materials.')

    def test_next_session_has_new_trial_records(self):
        self.setup_for()
        _, first = start_session(self.staff_request, SessionStartRequest(client_id=self.client_row.id))
        TrialEvent.objects.create(session_run_id=first['id'], target_id=self.targets[0].id, target_name='Synthetic target', trial_number=1, response_score=1, prompt_level_label='Independent', recorded_at=timezone.now())
        _, second = start_session(self.staff_request, SessionStartRequest(client_id=self.client_row.id))
        self.assertNotEqual(first['id'], second['id'])
        self.assertFalse(TrialEvent.objects.filter(session_run_id=second['id']).exists())
        self.assertEqual(TrialEvent.objects.filter(session_run_id=first['id']).count(), 1)

    def test_maintenance_is_hidden_until_due(self):
        workflow = WorkflowTemplate.objects.create(name='Weekly maintenance', phases=[{'phase': 'mastered', 'maintenance': {'intervals': [{'type': 'weekly', 'value': 1}]}}])
        target = self.targets[0]
        target.status = 'mastered'
        target.workflow_template = workflow
        target.maintenance_episodes_completed = 1
        target.save()
        run = SessionRun.objects.create(external_client_id=501, staff=self.staff, status='submitted', submitted_at=timezone.now())
        TrialEvent.objects.create(session_run=run, target_id=target.id, target_name=target.name, trial_number=1, response_score=1, prompt_level_label='Independent', recorded_at=timezone.now())
        self.assertFalse(_target_due_for_session(target))
        with patch('apps.sessions.services.timezone.now', return_value=timezone.now() + timedelta(days=8)):
            self.assertTrue(_target_due_for_session(target))

    def test_setup_with_only_closed_programs_cannot_create_empty_run(self):
        setup = self.setup_for()
        Program.objects.filter(pk__in=[p.id for p in self.programs]).update(phase='closed')
        with self.assertRaises(HttpError) as error:
            start_session(self.staff_request, SessionStartRequest(client_id=self.client_row.id, lesson_id=setup.id))
        self.assertEqual(error.exception.status_code, 400)
        self.assertFalse(SessionRun.objects.exists())

    def test_assigned_supervisor_can_record_other_providers_appointment(self):
        supervisor = User.objects.create_user('recording-supervisor@example.test', role='supervisor', organization=self.org, external_admin_id=101)
        ClientStaffAssignment.objects.create(client=self.client_row, user=supervisor)
        request = SimpleNamespace(user=supervisor, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        self.setup_for()
        status, result = start_session(request, SessionStartRequest(client_id=self.client_row.id, appointment_id=self.appt.id))
        self.assertEqual(status, 201)
        self.assertEqual(result['staff_id'], supervisor.id)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.staff_id, self.staff.id)

    def test_supervisor_start_still_requires_permission(self):
        supervisor = User.objects.create_user('blocked-supervisor@example.test', role='supervisor', organization=self.org, external_admin_id=101)
        ClientStaffAssignment.objects.create(client=self.client_row, user=supervisor)
        RolePermission.objects.create(organization=self.org, role='supervisor', permissions={'session_start': False})
        request = SimpleNamespace(user=supervisor, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        with self.assertRaises(HttpError) as error:
            start_session(request, SessionStartRequest(client_id=self.client_row.id, appointment_id=self.appt.id))
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(SessionRun.objects.exists())

    def test_supervisor_cannot_record_client_from_another_practice(self):
        supervisor = User.objects.create_user('scoped-supervisor@example.test', role='supervisor', organization=self.org, external_admin_id=101)
        request = SimpleNamespace(user=supervisor, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        foreign = Client.objects.create(first_name='Synthetic', last_name='Other practice', external_admin_id=202)
        with self.assertRaises(HttpError) as error:
            start_session(request, SessionStartRequest(client_id=foreign.id))
        self.assertEqual(error.exception.status_code, 404)
        self.assertFalse(SessionRun.objects.exists())
