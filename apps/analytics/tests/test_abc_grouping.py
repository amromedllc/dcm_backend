from datetime import date, datetime, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.analytics.services import get_abc_data_by_day
from apps.sessions.models import ABCEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class ABCGroupingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-abc-grouping', schema_name='test_org_abc_grouping',
        )
        self.client_id = 123
        self.day = date(2026, 1, 1)
        when = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            ABCEvent.objects.create(
                external_client_id=self.client_id,
                occurred_at=when,
                antecedent='Denied access',
                behavior_description='Aggression',
                consequence='Redirected',
                setting='Clinic',
                duration_seconds=60,
            )
            ABCEvent.objects.create(
                external_client_id=self.client_id,
                occurred_at=when,
                antecedent='Transition',
                behavior_description='Aggression',
                consequence='Break offered',
                setting='Clinic',
                duration_seconds=30,
            )

    def test_group_by_behavior_collapses_matching_events(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_abc_data_by_day(self.client_id, self.day, self.day, group_by='behavior')
            self.assertEqual(len(points), 1)
            self.assertEqual(points[0]['series_name'], 'Aggression')
            self.assertEqual(points[0]['count'], 2)
            self.assertEqual(points[0]['total_duration_seconds'], 90)

    def test_filter_by_antecedent_limits_results(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_abc_data_by_day(
                self.client_id,
                self.day,
                self.day,
                group_by='consequence',
                antecedent='transition',
            )
            self.assertEqual(len(points), 1)
            self.assertEqual(points[0]['series_name'], 'Break offered')
            self.assertEqual(points[0]['count'], 1)
