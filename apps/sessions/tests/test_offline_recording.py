from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from django_tenants.utils import schema_context
from ninja.errors import HttpError
from pydantic import ValidationError
from apps.tenants.models import Organization
from apps.sessions import api
from apps.sessions.models import SessionRun, TrialEvent
from apps.sessions.schemas import TrialEventCreateRequest, TrialEventSchema, SessionSyncPayload
from shared.tenancy import tenant_context

class OfflineSchemaTests(SimpleTestCase):
    def test_invalid_event_ids_are_rejected(self):
        base = dict(target_id=1, target_name='Target', trial_number=1, response_score=1,
                    prompt_level_label='Independent', recorded_at=timezone.now())
        for value in ['', 'x' * 65]:
            with self.assertRaises(ValidationError): TrialEventCreateRequest(client_event_id=value, **base)
        self.assertIsNone(TrialEventCreateRequest(**base).client_event_id)

class OfflineTrialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Offline trials', slug='offline-trials', schema_name='offline_trials')

    def setUp(self):
        self.schema = schema_context(self.org.schema_name)
        self.tenant = tenant_context(self.org.pk)
        self.schema.__enter__(); self.tenant.__enter__()
        self.addCleanup(self.schema.__exit__, None, None, None)
        self.addCleanup(self.tenant.__exit__, None, None, None)
        self.session = SessionRun.objects.create(external_client_id=1)
        self.data = TrialEventCreateRequest(client_event_id='event-1', target_id=1, target_name='Target',
            trial_number=1, response_score=1, prompt_level_label='Independent', recorded_at=timezone.now())

    def test_lost_response_retry_returns_original_trial(self):
        first, created = api._get_or_create_trial(self.session.pk, self.data.dict())
        self.assertTrue(created)
        second, created = api._get_or_create_trial(self.session.pk, self.data.dict())
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TrialEvent.objects.count(), 1)
        self.assertEqual(TrialEventSchema.from_orm(second).client_event_id, 'event-1')

    def test_same_event_with_different_score_is_a_conflict(self):
        api._get_or_create_trial(self.session.pk, self.data.dict())
        data = self.data.dict(); data['response_score'] = 0
        with self.assertRaises(HttpError) as error: api._get_or_create_trial(self.session.pk, data)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(TrialEvent.objects.get().response_score, 1)

    def test_trial_number_collision_is_a_conflict_not_data_overwrite(self):
        api._get_or_create_trial(self.session.pk, self.data.dict())
        data = self.data.dict(); data['client_event_id'] = 'different-event'
        with self.assertRaises(HttpError) as error: api._get_or_create_trial(self.session.pk, data)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(TrialEvent.objects.count(), 1)

    def test_event_ids_are_scoped_to_the_session(self):
        other = SessionRun.objects.create(external_client_id=2)
        a, _ = api._get_or_create_trial(self.session.pk, self.data.dict())
        b, _ = api._get_or_create_trial(other.pk, self.data.dict())
        self.assertNotEqual(a.pk, b.pk)

    def test_legacy_clients_still_create_trials_without_uuid(self):
        self.data.client_event_id = None
        row, created = api._get_or_create_trial(self.session.pk, self.data.dict())
        self.assertTrue(created)
        self.assertIsNone(row.client_event_id)

    def test_post_endpoint_retries_do_not_duplicate(self):
        with patch.object(api, '_session_for_data_change', return_value=(self.session, False)):
            _, first = api.add_trial(SimpleNamespace(), self.session.pk, self.data)
            _, second = api.add_trial(SimpleNamespace(), self.session.pk, self.data)
        self.assertEqual(first.pk, second.pk)

    def test_access_rejection_does_not_create_an_event(self):
        with patch.object(api, '_session_for_data_change', side_effect=HttpError(403, 'Denied')):
            with self.assertRaises(HttpError): api.add_trial(SimpleNamespace(), self.session.pk, self.data)
        self.assertFalse(TrialEvent.objects.exists())

    def test_mobile_sync_preserves_legacy_and_uuid_idempotency(self):
        for use_uuid in [False, True]:
            data = self.data.copy(deep=True)
            data.trial_number = 2 if use_uuid else 1
            data.client_event_id = 'mobile-event' if use_uuid else None
            with patch.object(api, '_require_recording_access', return_value=self.session):
                first = api.sync_session(SimpleNamespace(), self.session.pk, SessionSyncPayload(trials=[data]))
                second = api.sync_session(SimpleNamespace(), self.session.pk, SessionSyncPayload(trials=[data]))
            self.assertEqual(first.trials_created, 1)
            self.assertEqual(second.trials_created, 0)
