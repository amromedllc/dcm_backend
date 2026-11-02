"""Scatterplot time bucketing, historical interpretation and access tests."""
from datetime import date, datetime, timezone as utc
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.test import SimpleTestCase
from django.utils import timezone
from ninja.errors import HttpError
from apps.analytics import api, scatterplot
from apps.programs.models import Target, allowed_measurements, default_measurement
from apps.sessions.api import _behavior_style_targets
from shared.tenancy import tenant_context


class ScatterplotTests(SimpleTestCase):
    def setUp(self):
        self.start, self.end = date(2026, 1, 1), date(2026, 1, 31)
        self.request = SimpleNamespace(user=SimpleNamespace(timezone='Asia/Kolkata'))

    def event(self, at, count=1, session=1):
        return {'occurred_at': datetime.fromisoformat(at), 'frequency_count': count, 'target_id': 1, 'session_run_id': session}

    def bucket(self, events, minutes=60, zone='UTC', start=None, end=None):
        return scatterplot.bucket_scatterplot(events, start or self.start, end or self.end, minutes, ZoneInfo(zone))

    def test_type_defaults_and_session_summary(self):
        self.assertIn('scatterplot', Target.MeasurementType.values)
        self.assertEqual(allowed_measurements('scatterplot'), ['frequency'])
        self.assertEqual(default_measurement('scatterplot'), 'frequency')
        self.assertEqual(_behavior_style_targets({'programs': [{'targets': [{'id': 1, 'measurement_type': 'scatterplot'}]}]}), {1: 'frequency'})

    def test_complete_empty_grid(self):
        cells = self.bucket([])
        self.assertEqual(len(cells), 24 * 7)
        self.assertEqual(cells[0], {'weekday': 0, 'minute_of_day': 0, 'count': 0})
        self.assertTrue(all(c['count'] == 0 for c in cells))

    def test_local_weekday_rollover_and_half_hour_grid(self):
        cells = self.bucket([self.event('2026-01-04T18:45:00+00:00', 3)], 30, 'Asia/Kolkata')
        self.assertEqual(len(cells), 48 * 7)
        self.assertEqual([c for c in cells if c['count']], [{'weekday': 0, 'minute_of_day': 0, 'count': 3}])

    def test_repeated_weekdays_sum_counts(self):
        cells = self.bucket([self.event('2026-01-05T10:01:00+00:00', 2), self.event('2026-01-12T10:59:59+00:00', 4)])
        self.assertEqual([c for c in cells if c['count']], [{'weekday': 0, 'minute_of_day': 600, 'count': 6}])

    def test_date_and_bucket_boundaries(self):
        cells = self.bucket([self.event('2026-01-05T10:29:59+00:00'), self.event('2026-01-05T10:30:00+00:00'), self.event('2025-12-31T23:59:59+00:00', 9), self.event('2026-02-01T00:00:00+00:00', 9), self.event('2026-01-31T23:59:59+00:00', 2)], 30)
        self.assertEqual(sum(c['count'] for c in cells), 4)
        self.assertEqual([(c['minute_of_day'], c['count']) for c in cells if c['count']], [(600, 1), (630, 1), (1410, 2)])

    def test_dst_repeated_hour_keeps_both_events(self):
        cells = self.bucket([self.event('2026-11-01T05:30:00+00:00'), self.event('2026-11-01T06:30:00+00:00')], zone='America/New_York', start=date(2026, 11, 1), end=date(2026, 11, 1))
        self.assertEqual([c for c in cells if c['count']], [{'weekday': 6, 'minute_of_day': 60, 'count': 2}])

    def test_invalid_bucket_size(self):
        with self.assertRaises(ValueError):
            self.bucket([], 45)

    def test_service_scopes_event_rows_and_historical_type(self):
        events = [self.event('2026-01-05T00:00:00+00:00', 3), self.event('2026-01-05T00:00:00+00:00', 9, 2)]
        snapshots = [{'id': i, 'program_snapshot': {'programs': [{'targets': [{'id': 1, 'measurement_type': kind}]}]}} for i, kind in [(1, 'scatterplot'), (2, 'frequency')]]
        with tenant_context(10), timezone.override('Asia/Kolkata'), patch.object(scatterplot.BehaviorEvent, 'objects') as events_manager, patch.object(scatterplot.SessionRun, 'objects') as sessions:
            events_manager.filter.return_value.values.return_value = events
            sessions.filter.return_value.values.return_value = snapshots
            data = scatterplot.get_scatterplot_data([1], [101], self.start, self.end, 60)
            filters = events_manager.filter.call_args.kwargs
            self.assertEqual(filters['target_id__in'], [1])
            self.assertEqual(filters['session_run__external_client_id__in'], [101])
            self.assertEqual(filters['session_run__organization_id'], 10)
            self.assertEqual(filters['occurred_at__gte'].astimezone(utc.utc), datetime(2025, 12, 31, 18, 30, tzinfo=utc.utc))
            self.assertEqual(filters['occurred_at__lt'].astimezone(utc.utc), datetime(2026, 1, 31, 18, 30, tzinfo=utc.utc))
            self.assertEqual(data['total_occurrences'], 3)
            self.assertEqual(data['timezone'], 'Asia/Kolkata')

    def test_permission_denial_stops_program_lookup(self):
        with patch('apps.accounts.permissions.require_permission', side_effect=HttpError(403, 'Denied')), patch('apps.programs.api._get_program_or_404') as program:
            with self.assertRaises(HttpError) as error:
                api.program_scatterplot(self.request, 1)
            self.assertEqual(error.exception.status_code, 403)
            program.assert_not_called()

    def test_inaccessible_program_stops_data_read(self):
        with patch('apps.accounts.permissions.require_permission'), patch('apps.programs.api._get_program_or_404', side_effect=HttpError(404, 'Not found')), patch.object(scatterplot, 'get_scatterplot_data') as service:
            with self.assertRaises(HttpError) as error:
                api.program_scatterplot(self.request, 999)
            self.assertEqual(error.exception.status_code, 404)
            service.assert_not_called()

    def test_invalid_dates_and_bins(self):
        with patch('apps.accounts.permissions.require_permission'), patch('apps.programs.api._get_program_or_404', return_value=SimpleNamespace(is_template=False)):
            for frm, to, minutes in [(self.end, self.start, 60), (self.start, date(2027, 2, 1), 60), (self.start, self.end, 45)]:
                with self.subTest(frm=frm, to=to, minutes=minutes), self.assertRaises(HttpError) as error:
                    api.program_scatterplot(self.request, 1, frm, to, bin_minutes=minutes)
                self.assertEqual(error.exception.status_code, 400)

    def test_unavailable_and_malformed_targets(self):
        with patch('apps.accounts.permissions.require_permission'), patch('apps.programs.api._get_program_or_404', return_value=SimpleNamespace(is_template=False)), patch.object(api.Target, 'objects') as targets, patch.object(scatterplot, 'get_scatterplot_data') as service:
            targets.filter.return_value.values_list.return_value = [1]
            with self.assertRaises(HttpError) as error:
                api.program_scatterplot(self.request, 1, self.start, self.end, target_ids='999')
            self.assertEqual(error.exception.status_code, 404)
            service.assert_not_called()
            for invalid in ['', 'abc', '1,-2']:
                with self.assertRaises(HttpError) as error:
                    api.program_scatterplot(self.request, 1, self.start, self.end, target_ids=invalid)
                self.assertEqual(error.exception.status_code, 400)
