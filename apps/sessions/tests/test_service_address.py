from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context
from ninja.errors import HttpError
from pydantic import ValidationError

from apps.accounts.models import User
from apps.sessions import api as sessions_api
from apps.sessions.models import SessionRun, TrialEvent
from apps.sessions.schemas import ServiceAddress, SessionStartRequest, SessionSubmitRequest, SessionSyncPayload, TrialEventCreateRequest
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


ADDRESS = {'street': '123 Main St', 'unit': 'Suite 200', 'city': 'Austin', 'state': 'TX', 'zip_code': '78701', 'country': 'US'}


class ServiceAddressValidationTests(SimpleTestCase):
    def test_normalizes_address_and_accepts_zip_plus_four(self):
        address = ServiceAddress(**{**ADDRESS, 'street': ' 123 Main St ', 'state': 'tx', 'zip_code': ' 78701-1234 '})
        self.assertEqual(address.street, '123 Main St')
        self.assertEqual(address.state, 'TX')
        self.assertEqual(address.zip_code, '78701-1234')

    def test_rejects_incomplete_or_invalid_us_addresses(self):
        for field, value in [('street', '  '), ('city', ''), ('state', 'ZZ'), ('zip_code', '7870'), ('zip_code', 'ABCDE'), ('zip_code', '787011234'), ('country', 'CA')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                ServiceAddress(**{**ADDRESS, field: value})


class ServiceAddressSubmissionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Address Org', slug='address-org', schema_name='address_org')
        self.staff = User.objects.create_user(email='address-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF)
        self.request = SimpleNamespace(user=self.staff, tenant=self.org)
        patcher = mock.patch.object(sessions_api, '_accessible_external_client_ids', return_value={1})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _start(self, setting, manual=False):
        now = timezone.now()
        payload = SessionStartRequest(
            client_id=1, service_setting=setting, manual=manual,
            started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1),
        )
        return sessions_api.start_session(self.request, payload)[1]

    def _trial(self):
        return TrialEventCreateRequest(
            target_id=5, target_name='T', trial_number=1, response_score=1,
            prompt_level_label='Independent', recorded_at=timezone.now(), client_event_id='address-trial',
        )

    def test_live_and_manual_submission_require_addresses_only_for_physical_settings(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            for manual in (False, True):
                for setting in SessionRun.ServiceSetting.values:
                    with self.subTest(manual=manual, setting=setting):
                        session = self._start(setting, manual)
                        sessions_api.add_trial(self.request, session['id'], self._trial())
                        needs_address = setting in ('Home', 'Office', 'School', 'Community')
                        if needs_address:
                            with self.assertRaises(HttpError) as raised:
                                sessions_api.submit(self.request, session['id'], SessionSubmitRequest())
                            self.assertEqual(raised.exception.status_code, 400)
                            self.assertEqual(SessionRun.objects.get(pk=session['id']).status, 'open')
                            self.assertEqual(TrialEvent.objects.filter(session_run_id=session['id']).count(), 1)
                        payload = SessionSubmitRequest(service_address=ServiceAddress(**ADDRESS) if needs_address else None)
                        result = sessions_api.submit(self.request, session['id'], payload)
                        saved = SessionRun.objects.get(pk=session['id'])
                        self.assertEqual(saved.status, 'submitted')
                        self.assertEqual(saved.service_address, ADDRESS if needs_address else {})
                        self.assertEqual(result['session']['service_address'], saved.service_address)

    def test_batch_sync_cannot_bypass_address_requirement_and_can_retry(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            session = self._start('School', manual=True)
            payload = SessionSyncPayload(trials=[self._trial()], submit_after_sync=True)
            with self.assertRaises(HttpError) as raised:
                sessions_api.sync_session(self.request, session['id'], payload)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(SessionRun.objects.get(pk=session['id']).status, 'open')
            payload.service_address = ServiceAddress(**ADDRESS)
            result = sessions_api.sync_session(self.request, session['id'], payload)
            saved = SessionRun.objects.get(pk=session['id'])
            self.assertTrue(result.submitted)
            self.assertEqual(saved.service_address, ADDRESS)
            self.assertEqual(TrialEvent.objects.filter(session_run_id=session['id']).count(), 1)
