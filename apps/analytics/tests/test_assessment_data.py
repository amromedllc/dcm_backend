from datetime import date

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.analytics.models import AssessmentRecord
from apps.analytics.services import get_assessment_data
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class AssessmentDataTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-assessment-data', schema_name='test_org_assessment_data',
        )
        self.client_id = 321
        self.day = date(2026, 1, 1)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            AssessmentRecord.objects.create(
                external_client_id=self.client_id,
                assessment_name='VB-MAPP',
                domain='Mand',
                metric='Milestones',
                assessed_on=self.day,
                score=8,
                max_score=10,
            )
            AssessmentRecord.objects.create(
                external_client_id=self.client_id,
                assessment_name='VB-MAPP',
                domain='Tact',
                metric='Milestones',
                assessed_on=self.day,
                score=5,
                max_score=10,
            )

    def test_group_by_domain_returns_percent_scores(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_assessment_data(self.client_id, self.day, self.day, group_by='domain')
            by_domain = {p['series_name']: p for p in points}
            self.assertEqual(set(by_domain.keys()), {'Mand', 'Tact'})
            self.assertEqual(by_domain['Mand']['pct_score'], 80.0)
            self.assertEqual(by_domain['Tact']['pct_score'], 50.0)

    def test_filter_by_domain_limits_results(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_assessment_data(self.client_id, self.day, self.day, group_by='metric', domain='mand')
            self.assertEqual(len(points), 1)
            self.assertEqual(points[0]['series_name'], 'Milestones')
            self.assertEqual(points[0]['score'], 8.0)
