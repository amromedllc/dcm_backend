"""Editing data in a session that is already submitted/approved."""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import RolePermission, User
from apps.audit.models import AuditLog
from apps.sessions import api as sessions_api
from apps.sessions.models import SessionRun, TrialEvent
from apps.sessions.schemas import TrialEventCreateRequest, SessionSyncPayload
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class ClosedSessionEditTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Edit Org', slug='edit-org', schema_name='edit_org')
        self.staff = User.objects.create_user(
            email='edit-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        self.supervisor = User.objects.create_user(
            email='edit-sup@example.com', password='x', organization=self.org, role=User.Role.SUPERVISOR,
        )
        self.admin = User.objects.create_user(
            email='edit-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )
        patcher = mock.patch.object(sessions_api, '_accessible_external_client_ids', return_value={1})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ctx(self):
        return schema_context(self.org.schema_name), tenant_context(self.org.pk)

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def _session(self, status):
        return SessionRun.objects.create(external_client_id=1, staff=self.staff, status=status)

    def _trial_payload(self, number=1):
        return TrialEventCreateRequest(
            target_id=5, target_name='Ask for help', trial_number=number, response_score=1,
            prompt_level_label='Independent', recorded_at=timezone.now(),
        )

    def test_staff_cannot_change_a_completed_session_by_default(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            session = self._session(SessionRun.Status.SUBMITTED)
            with self.assertRaises(HttpError) as raised:
                sessions_api.add_trial(self._request(self.staff), session.id, self._trial_payload(), reason='typo')
            self.assertEqual(raised.exception.status_code, 403)

    def test_a_reason_is_required(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            session = self._session(SessionRun.Status.SUBMITTED)
            with self.assertRaises(HttpError) as raised:
                sessions_api.add_trial(self._request(self.supervisor), session.id, self._trial_payload(), reason='  ')
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(TrialEvent.objects.count(), 0)

    def test_add_and_delete_are_applied_and_audited(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            session = self._session(SessionRun.Status.SUBMITTED)
            request = self._request(self.supervisor)
            _status, trial = sessions_api.add_trial(request, session.id, self._trial_payload(), reason='Missed trial')
            self.assertEqual(TrialEvent.objects.filter(session_run=session).count(), 1)
            sessions_api.delete_trial(request, session.id, trial.id, reason='Entered twice')
            self.assertEqual(TrialEvent.objects.filter(session_run=session).count(), 0)

            logs = list(AuditLog.objects.filter(model='SessionRun', object_id=str(session.id)).order_by('id'))
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0].actor_email, 'edit-sup@example.com')
        self.assertTrue(logs[0].changes['edited_after_completion'])
        self.assertEqual(logs[0].changes['reason'], 'Missed trial')
        self.assertIn('Added trial 1', logs[0].changes['change'])
        self.assertIn('Deleted trial 1', logs[1].changes['change'])

    def test_staff_with_edit_permission_cannot_change_approved_data(self):
        RolePermission.objects.update_or_create(
            organization=self.org, role=User.Role.STAFF,
            defaults={'permissions': {'sessions_edit_data': True, 'session_approve': False}},
        )
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            session = self._session(SessionRun.Status.APPROVED)
            session.reviewed_by = self.supervisor
            session.reviewed_at = timezone.now()
            session.save()
            with self.assertRaises(HttpError) as raised:
                sessions_api.add_trial(self._request(self.staff), session.id, self._trial_payload(), reason='Fix')
            self.assertEqual(raised.exception.status_code, 409)
            session.refresh_from_db()
            self.assertEqual(session.status, SessionRun.Status.APPROVED)
            self.assertEqual(session.reviewed_by_id, self.supervisor.id)
            self.assertIsNotNone(session.reviewed_at)
            self.assertFalse(TrialEvent.objects.filter(session_run=session).exists())
            self.assertFalse(AuditLog.objects.filter(object_id=str(session.id)).exists())

    def test_supervisor_and_admin_cannot_add_or_delete_approved_recording_data(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            session = self._session(SessionRun.Status.APPROVED)
            trial = TrialEvent.objects.create(session_run=session, **self._trial_payload().dict())
            for user in (self.supervisor, self.admin):
                for action in (
                    lambda request: sessions_api.add_trial(request, session.id, self._trial_payload(), reason='Fix'),
                    lambda request: sessions_api.delete_trial(request, session.id, trial.id, reason='Fix'),
                    lambda request: sessions_api.add_behavior(request, session.id, None, reason='Fix'),
                    lambda request: sessions_api.delete_behavior(request, session.id, 999, reason='Fix'),
                    lambda request: sessions_api.add_abc(request, session.id, None, reason='Fix'),
                    lambda request: sessions_api.delete_abc(request, session.id, 999, reason='Fix'),
                ):
                    with self.subTest(role=user.role, action=action), self.assertRaises(HttpError) as raised:
                        action(self._request(user))
                    self.assertEqual(raised.exception.status_code, 409)
            self.assertTrue(TrialEvent.objects.filter(pk=trial.pk).exists())
            session.refresh_from_db()
            self.assertEqual(session.status, SessionRun.Status.APPROVED)

    def test_open_sessions_need_no_reason_and_write_no_audit(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            session = self._session(SessionRun.Status.OPEN)
            sessions_api.add_trial(self._request(self.staff), session.id, self._trial_payload())
            self.assertEqual(TrialEvent.objects.filter(session_run=session).count(), 1)
            self.assertEqual(AuditLog.objects.count(), 0)

    def test_offline_sync_cannot_change_approved_data(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            session = self._session(SessionRun.Status.APPROVED)
            for user in (self.staff, self.supervisor, self.admin):
                with self.subTest(role=user.role), self.assertRaises(HttpError) as raised:
                    sessions_api.sync_session(self._request(user), session.id, SessionSyncPayload(trials=[self._trial_payload()]))
                self.assertEqual(raised.exception.status_code, 409)
            self.assertFalse(TrialEvent.objects.filter(session_run=session).exists())
            session.refresh_from_db()
            self.assertEqual(session.status, SessionRun.Status.APPROVED)


class EditImpactTests(TestCase):
    """The list shown after editing: automatic target changes this session caused."""

    def setUp(self):
        from apps.programs.models import Program, Target

        self.org = Organization.objects.create(name='Impact Org', slug='impact-org', schema_name='impact_org')
        self.supervisor = User.objects.create_user(
            email='impact-sup@example.com', password='x', organization=self.org, role=User.Role.SUPERVISOR,
        )
        self.staff = User.objects.create_user(
            email='impact-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        patcher = mock.patch.object(sessions_api, '_accessible_external_client_ids', return_value={1})
        patcher.start()
        self.addCleanup(patcher.stop)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            program = Program.objects.create(name='Manding', category='skill_acquisition', external_client_id=1)
            self.target = Target.objects.create(
                program=program, name='Ask for help', measurement_type='discrete_trial', status='maintenance',
            )
            self.session = SessionRun.objects.create(external_client_id=1, staff=self.staff, status='approved')
            self.other_session = SessionRun.objects.create(external_client_id=1, staff=self.staff, status='approved')

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def _ctx(self):
        return schema_context(self.org.schema_name), tenant_context(self.org.pk)

    def test_lists_only_automatic_changes_caused_by_this_session(self):
        from apps.programs.models import TargetPromptLevelChange, TargetStatusChange

        ctx = self._ctx()
        with ctx[0], ctx[1]:
            TargetStatusChange.objects.create(
                target=self.target, from_status='teaching', to_status='maintenance',
                trigger='auto_mastery', session_run_id=self.session.id,
            )
            TargetPromptLevelChange.objects.create(
                target=self.target, from_level_index=2, to_level_index=1,
                from_level_label='Model', to_level_label='Gesture',
                trigger='auto_fading', session_run_id=self.session.id,
            )
            # not shown: manual change, and a change caused by a different session
            TargetStatusChange.objects.create(
                target=self.target, from_status='maintenance', to_status='hold',
                trigger='manual', session_run_id=self.session.id,
            )
            TargetStatusChange.objects.create(
                target=self.target, from_status='baseline', to_status='teaching',
                trigger='auto_mastery', session_run_id=self.other_session.id,
            )
            result = sessions_api.session_edit_impact(self._request(self.supervisor), self.session.id)
        by_kind = {item['kind']: item for item in result}
        self.assertEqual(len(result), 2)
        self.assertEqual(by_kind['status']['from_value'], 'teaching')
        self.assertEqual(by_kind['status']['to_value'], 'maintenance')
        self.assertEqual(by_kind['status']['current_value'], 'maintenance')
        self.assertEqual(by_kind['prompt_level']['to_value'], 'Gesture')
        self.assertEqual(by_kind['prompt_level']['target_name'], 'Ask for help')

    def test_empty_when_the_session_changed_nothing(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            self.assertEqual(sessions_api.session_edit_impact(self._request(self.supervisor), self.session.id), [])

    def test_needs_the_edit_privilege(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            with self.assertRaises(HttpError) as raised:
                sessions_api.session_edit_impact(self._request(self.staff), self.session.id)
        self.assertEqual(raised.exception.status_code, 403)
