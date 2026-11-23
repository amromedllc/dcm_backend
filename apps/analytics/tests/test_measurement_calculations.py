"""Synthetic chart regression cases; service tests mock database queries."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo
from apps.analytics.measurement_windows import chart_rate, session_seconds_on_day, session_window_statistics


class ObservationWindowTests(TestCase):
    def test_full_window_and_subminute_rate(self):
        start = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
        seconds = session_seconds_on_day(start, start + timedelta(hours=1), start.date(), timezone.utc)
        self.assertEqual(chart_rate(6, seconds, True), 6)
        self.assertEqual(chart_rate(6, seconds), 0.1)
        self.assertEqual(chart_rate(1, 30), 2)

    def test_unknown_window_is_not_count_or_zero_rate(self):
        self.assertIsNone(chart_rate(6, None))
        self.assertIsNone(chart_rate(0, 0))
        self.assertEqual(chart_rate(0, 60), 0)

    def test_midnight_clipping(self):
        zone = ZoneInfo('Asia/Kolkata')
        start = datetime(2026, 11, 1, 23, 30, tzinfo=zone)
        end = start + timedelta(hours=1)
        for day in [date(2026, 11, 1), date(2026, 11, 2)]:
            self.assertEqual(session_seconds_on_day(start, end, day, zone), 1800)

    def test_dst_uses_elapsed_time(self):
        zone = ZoneInfo('America/New_York')
        start = datetime(2026, 11, 1, tzinfo=zone)
        self.assertEqual(session_seconds_on_day(start, datetime(2026, 11, 2, tzinfo=zone), start.date(), zone), 90000)

    def test_calendar_windows_include_silent_session_time(self):
        zone = timezone.utc
        start = datetime(2026, 11, 2, 23, 30, tzinfo=zone)
        result = session_window_statistics(1, start, start + timedelta(hours=2), start.date(), zone)
        self.assertEqual(result['daily'], 1800)
        self.assertEqual(result['weekly'], 7200)
        self.assertEqual(result['monthly'], 7200)

    def test_calendar_rate_denominator_respects_selected_date_range(self):
        start = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
        result = session_window_statistics(1, start, start + timedelta(hours=2), start.date(), timezone.utc, start.date(), start.date())
        self.assertEqual(result['weekly'], 1800)
        self.assertEqual(result['monthly'], 1800)
        self.assertEqual(result['session'], 1800)

    def test_month_boundary_and_repeated_clock_hour(self):
        zone = ZoneInfo('America/New_York')
        start = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=zone)
        end = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=zone)
        self.assertEqual(session_seconds_on_day(start, end, start.date(), zone), 3600)
        start = datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc)
        result = session_window_statistics(1, start, start + timedelta(hours=2), start.date(), timezone.utc)
        self.assertEqual(result['monthly'], 1800)
        self.assertEqual(result['session'], 7200)


class BehaviorServiceTests(TestCase):
    def calculate(self, measurement, events, sessions):
        from apps.analytics import services
        target = SimpleNamespace(id=1, module_id=None, submodule_id=None, measurement=measurement)
        with patch.object(services.BehaviorEvent, 'objects') as em, patch.object(services.Target, 'objects') as tm, \
             patch.object(services.SessionRun, 'objects') as sm, \
             patch.object(services, '_module_name_map', return_value={}), \
             patch.object(services, '_submodule_name_map', return_value={}), \
             patch.object(services, '_target_rate_data', return_value=[]), services.dj_timezone.override(timezone.utc):
            em.filter.return_value.filter.return_value.values.return_value = events
            tm.filter.return_value.only.return_value = [target]
            sm.filter.return_value.only.return_value = sessions
            result = services.get_behavior_data_by_day([1], date(2026, 11, 1), date(2026, 11, 1))
            em.filter.assert_called_once_with(observation_ended_at__isnull=True)
            self.assertEqual(em.filter.return_value.filter.call_args.kwargs['target_id__in'], [1])
            return result[0]

    def event(self, session=10, duration=None, count=1):
        return {'occurred_at__date': date(2026, 11, 1), 'occurred_at': datetime(2026, 11, 1, 9, tzinfo=timezone.utc),
                'session_run_id': session, 'target_id': 1, 'target_name': 'Synthetic target',
                'frequency_count': count, 'duration_seconds': duration}

    def session(self, id=10, minutes=60):
        start = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
        return SimpleNamespace(id=id, started_at=start, ended_at=start + timedelta(minutes=minutes) if minutes else None,
                               submitted_at=None, reviewed_at=None)

    def test_rate_deduplicates_windows_across_events(self):
        result = self.calculate('rate_per_hour', [self.event(count=2), self.event(), self.event(session=20, count=3)], [self.session(), self.session(20, 30)])
        self.assertEqual(result['observation_seconds'], 5400)
        self.assertEqual(result['measurement_value'], 4)

    def test_unknown_window(self):
        result = self.calculate('rate_per_minute', [self.event(count=5)], [self.session(minutes=0)])
        self.assertIsNone(result['measurement_value'])
        self.assertIsNone(result['observation_seconds'])

    def test_duration_statistics_and_frequency(self):
        events = [self.event(duration=0), self.event(duration=30), self.event(duration=60)]
        for measurement, expected in [('frequency', 3), ('total_observed_duration', 90), ('min_observed_duration', 0), ('max_observed_duration', 60), ('avg_observed_duration', 30)]:
            with self.subTest(measurement=measurement):
                result = self.calculate(measurement, events, [self.session()])
                self.assertEqual(result['measurement_value'], expected)
                self.assertEqual(result['duration_count'], 3)

    def test_occurrences_are_not_percent_correct(self):
        self.assertIsNone(self.calculate('percent_correct', [self.event()], [self.session()])['measurement_value'])


class TrialServiceTests(TestCase):
    def calculate(self, measurement='frequency', child=None, seconds=None, score=1):
        from apps.analytics import services
        start = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
        target = SimpleNamespace(id=1, module_id=None, submodule_id=None, measurement=measurement)
        session = SimpleNamespace(id=10, started_at=start, ended_at=start + timedelta(hours=1), submitted_at=None, reviewed_at=None)
        event = {'recorded_at__date': start.date(), 'target_id': 1, 'target_name': 'Synthetic target',
                 'response_score': score, 'sub_item_key': 'step' if child else '', 'session_run_id': 10,
                 'prompt_level_label': 'Synthetic prompt', 'session_run__started_at': start, 'value_seconds': seconds}
        with patch.object(services.TrialEvent, 'objects') as em, patch.object(services.Target, 'objects') as tm, \
             patch.object(services.SessionRun, 'objects') as sm, \
             patch.object(services, '_module_name_map', return_value={}), \
             patch.object(services, '_submodule_name_map', return_value={}), \
             patch.object(services, '_max_scores_for_targets', return_value={1: 2}), \
             patch.object(services, '_prompt_rank_maps', return_value={}), \
             patch.object(services, '_sub_item_series', return_value={(1, 'step'): child} if child else {}), \
             services.dj_timezone.override(timezone.utc):
            em.filter.return_value.values.return_value = [event]
            tm.filter.return_value.only.return_value = [target]
            sm.filter.return_value.only.return_value = [session]
            result = services.get_trial_data_by_day([1], start.date(), start.date())
            self.assertEqual(em.filter.call_args.kwargs['target_id__in'], [1])
            return result[0]

    def test_configured_measurement_and_success_threshold_are_returned(self):
        result = self.calculate(score=1)
        self.assertEqual(result['measurement'], 'frequency')
        self.assertEqual(result['correct_count'], 0)
        self.assertEqual(result['observation_seconds'], 3600)
        self.assertEqual(self.calculate(score=2)['correct_count'], 1)
        from apps.analytics.schemas import TrialDataPointSchema
        TrialDataPointSchema(**result)

    def test_duration_child_retains_seconds_and_own_scoring_template(self):
        child = {'series_id': -11, 'name': 'Synthetic step', 'measurement': 'avg_observed_duration', 'success_score': 1}
        result = self.calculate(child=child, seconds=12.5)
        self.assertEqual(result['target_id'], -11)
        self.assertEqual(result['measurement'], 'avg_observed_duration')
        self.assertEqual(result['duration_count'], 1)
        self.assertEqual(result['total_duration_seconds'], 12.5)
        self.assertEqual(result['min_duration_seconds'], 12.5)
        self.assertEqual(result['max_duration_seconds'], 12.5)
        self.assertEqual(result['correct_count'], 1)

    def test_nullable_rate_is_valid_api_data(self):
        from apps.analytics.schemas import BehaviorDataPointSchema
        sample = dict(date=date(2026, 11, 1), target_id=1, target_name='Synthetic rate',
                      module_id=None, module_name=None, submodule_id=None, submodule_name=None,
                      frequency=1, total_duration_seconds=0, measurement='rate_per_minute', measurement_value=None)
        self.assertIsNone(BehaviorDataPointSchema(**sample).measurement_value)
