"""Observed periods are explicit, bounded, retryable, and distinct from occurrences."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from ninja.errors import HttpError
from pydantic import ValidationError
from apps.sessions import api
from apps.sessions.schemas import BehaviorEventCreateRequest, SessionSyncPayload


class ScatterplotObservationTests(SimpleTestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
        self.end = self.start + timedelta(hours=1)
        self.session = SimpleNamespace(started_at=self.start, ended_at=self.end, program_snapshot={
            'programs': [{'targets': [{'id': 1, 'measurement_type': 'scatterplot'}]}]})

    def payload(self, **overrides):
        values = dict(target_id=1, target_name='Synthetic target', occurred_at=self.start,
                      observation_ended_at=self.end, frequency_count=0, client_event_id='synthetic-observation')
        values.update(overrides)
        return BehaviorEventCreateRequest(**values)

    def test_roundtrip_and_batch_preserve_explicit_zero_coverage(self):
        data = self.payload()
        batch = SessionSyncPayload(behaviors=[data])
        self.assertEqual(batch.behaviors[0].observation_ended_at, self.end)
        self.assertEqual(data.model_dump()['frequency_count'], 0)
        self.assertEqual(BehaviorEventCreateRequest(**data.model_dump()).observation_ended_at, self.end)

    def test_invalid_intervals_are_rejected(self):
        cases = [dict(frequency_count=1), dict(duration_seconds=60), dict(severity='mild'),
                 dict(client_event_id=None), dict(observation_ended_at=self.start),
                 dict(observation_ended_at=self.start-timedelta(seconds=1)),
                 dict(observation_ended_at=self.start+timedelta(hours=25)),
                 dict(occurred_at=self.start.replace(tzinfo=None))]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValidationError):
                self.payload(**case)

    def test_unknown_or_other_measurement_target_rejected(self):
        for target in [None, {'id': 1, 'measurement_type': 'frequency'}]:
            self.session.program_snapshot = {'programs': [{'targets': [target] if target else []}]}
            with self.assertRaises(HttpError) as error:
                api._validate_scatterplot_observation(self.session, self.payload())
            self.assertEqual(error.exception.status_code, 400)

    def test_outside_session_and_future_windows_rejected(self):
        with patch.object(api.timezone, 'now', return_value=self.end):
            api._validate_scatterplot_observation(self.session, self.payload())
            for changes in [dict(occurred_at=self.start-timedelta(seconds=1)), dict(observation_ended_at=self.end+timedelta(seconds=1))]:
                with self.assertRaises(HttpError):
                    api._validate_scatterplot_observation(self.session, self.payload(**changes))
            self.session.ended_at = None
            with self.assertRaises(HttpError):
                api._validate_scatterplot_observation(self.session, self.payload(observation_ended_at=self.end+timedelta(seconds=1)))

    def test_duplicate_id_acknowledges_only_identical_observation(self):
        data = self.payload()
        existing = SimpleNamespace(**data.dict())
        with patch.object(api, '_validate_scatterplot_observation'), patch.object(api.SessionRun, 'objects'), patch.object(api.BehaviorEvent, 'objects') as events:
            events.get_or_create.return_value = (existing, False)
            self.assertEqual(api._get_or_create_behavior(1, data), (existing, False))
            with self.assertRaises(HttpError) as error:
                api._get_or_create_behavior(1, self.payload(observation_ended_at=self.end-timedelta(minutes=1)))
            self.assertEqual(error.exception.status_code, 409)
            with self.assertRaises(HttpError):
                api._get_or_create_behavior(1, BehaviorEventCreateRequest(target_id=1, target_name='Synthetic target', occurred_at=self.start, client_event_id=data.client_event_id))

    def test_observation_only_session_does_not_enter_automatic_mastery_history(self):
        from apps.programs import services
        with patch.object(api.SessionRun, 'objects') as sessions:
            sessions.filter.return_value.distinct.return_value.order_by.return_value.__getitem__.return_value = []
            services._recent_sessions_for_target(SimpleNamespace(id=1, measurement_type='scatterplot'), 3)
            self.assertTrue(sessions.filter.call_args.kwargs['behavior_events__observation_ended_at__isnull'])
