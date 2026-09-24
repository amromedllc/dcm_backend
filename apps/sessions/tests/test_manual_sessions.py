"""Manual session entry: privileges, time-window validation and stamping."""
import inspect
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import RolePermission, User
from apps.sessions import api as sessions_api
from apps.sessions.models import SessionRun, TrialEvent
from apps.sessions.schemas import SessionStartRequest, TrialEventCreateRequest
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

CLIENT_ID = 1


class ManualSessionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Manual Org', slug='manual-org', schema_name='manual_org')
        self.staff = User.objects.create_user(
            email='manual-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        patcher = mock.patch.object(sessions_api, '_accessible_external_client_ids', return_value={CLIENT_ID})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = timezone.now()

    def _request(self, user=None):
        return SimpleNamespace(user=user or self.staff, tenant=self.org)

    def _set_permissions(self, **permissions):
        RolePermission.objects.update_or_create(
            organization=self.org, role=User.Role.STAFF, defaults={'permissions': permissions},
        )

    def _start_manual(self, hours_ago=3, length_hours=1, **overrides):
        started = self.now - timedelta(hours=hours_ago)
        payload = dict(
            client_id=CLIENT_ID, manual=True, started_at=started, ended_at=started + timedelta(hours=length_hours),
        )
        payload.update(overrides)
        return sessions_api.start_session(self._request(), SessionStartRequest(**payload))

    def _in_tenant(self):
        return schema_context(self.org.schema_name), tenant_context(self.org.pk)

    def test_creates_manual_session_with_entered_window(self):
        schema, tenant = self._in_tenant()
        with schema, tenant:
            _status, session = self._start_manual()
            row = SessionRun.objects.get(pk=session['id'])
        self.assertEqual(row.entry_method, SessionRun.EntryMethod.MANUAL)
        self.assertEqual(row.ended_at - row.started_at, timedelta(hours=1))
        self.assertEqual(session['entry_method'], 'manual')

    def test_live_sessions_are_unchanged(self):
        schema, tenant = self._in_tenant()
        with schema, tenant:
            _status, session = sessions_api.start_session(self._request(), SessionStartRequest(client_id=CLIENT_ID))
            row = SessionRun.objects.get(pk=session['id'])
        self.assertEqual(row.entry_method, SessionRun.EntryMethod.LIVE)
        self.assertIsNone(row.ended_at)

    def test_window_validation(self):
        schema, tenant = self._in_tenant()
        cases = {
            'missing times': dict(started_at=None, ended_at=None),
            'ends in the future': dict(hours_ago=0, length_hours=2),
            'ends before it starts': dict(length_hours=-1),
            'longer than 24 hours': dict(hours_ago=30, length_hours=25),
            'more than a year ago': dict(hours_ago=24 * 400, length_hours=1),
            'no timezone': dict(started_at=(self.now - timedelta(hours=3)).replace(tzinfo=None),
                                ended_at=(self.now - timedelta(hours=2)).replace(tzinfo=None)),
        }
        with schema, tenant:
            for label, overrides in cases.items():
                with self.subTest(label):
                    with self.assertRaises(HttpError) as ctx:
                        self._start_manual(**overrides)
                    self.assertEqual(ctx.exception.status_code, 400)

    def test_manual_entry_needs_its_own_privilege(self):
        self._set_permissions(sessions_manual_entry=False)
        schema, tenant = self._in_tenant()
        with schema, tenant:
            with self.assertRaises(HttpError) as ctx:
                self._start_manual()
            self.assertEqual(ctx.exception.status_code, 403)
            # live recording is a separate privilege and still works
            sessions_api.start_session(self._request(), SessionStartRequest(client_id=CLIENT_ID))

    def test_manual_entry_works_without_live_recording_privilege(self):
        self._set_permissions(session_start=False, sessions_manual_entry=True)
        schema, tenant = self._in_tenant()
        with schema, tenant:
            _status, session = self._start_manual()
            when = self.now - timedelta(hours=2, minutes=30)
            sessions_api.add_trial(self._request(), session['id'], TrialEventCreateRequest(
                target_id=5, target_name='T', trial_number=1, response_score=1,
                prompt_level_label='Independent', recorded_at=when,
            ))
            self.assertEqual(TrialEvent.objects.filter(session_run_id=session['id']).count(), 1)
            with self.assertRaises(HttpError):
                sessions_api.start_session(self._request(), SessionStartRequest(client_id=CLIENT_ID))

    def test_live_session_data_still_needs_session_start(self):
        schema, tenant = self._in_tenant()
        with schema, tenant:
            _status, live = sessions_api.start_session(self._request(), SessionStartRequest(client_id=CLIENT_ID))
            self._set_permissions(session_start=False, sessions_manual_entry=True)
            with self.assertRaises(HttpError) as ctx:
                sessions_api.add_trial(self._request(), live['id'], TrialEventCreateRequest(
                    target_id=5, target_name='T', trial_number=1, response_score=1,
                    prompt_level_label='Independent', recorded_at=self.now,
                ))
            self.assertEqual(ctx.exception.status_code, 403)

    def test_trial_times_are_kept_inside_the_session_window(self):
        schema, tenant = self._in_tenant()
        with schema, tenant:
            _status, session = self._start_manual()
            row = SessionRun.objects.get(pk=session['id'])
            for number, stamp in enumerate((self.now, self.now - timedelta(days=5)), start=1):
                sessions_api.add_trial(self._request(), session['id'], TrialEventCreateRequest(
                    target_id=5, target_name='T', trial_number=number, response_score=1,
                    prompt_level_label='Independent', recorded_at=stamp,
                ))
            times = sorted(TrialEvent.objects.filter(session_run_id=session['id']).values_list('recorded_at', flat=True))
        self.assertEqual(times, [row.started_at, row.ended_at])
