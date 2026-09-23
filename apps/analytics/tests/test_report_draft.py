"""Server-side progress report drafts: save, reload, and permissions."""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.analytics import api as analytics_api
from apps.analytics.schemas import ClientReportDraftSaveRequest
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class ReportDraftApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Draft Org', slug='draft-org', schema_name='draft_org')
        self.admin = User.objects.create_user(
            email='draft-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email='draft-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        self.accessible = mock.patch('apps.programs.api._assert_client_accessible', return_value=None)
        self.accessible.start()
        self.addCleanup(self.accessible.stop)

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def test_empty_when_nothing_saved(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            result = analytics_api.get_client_report_draft(self._request(self.admin), 7)
        self.assertIsNone(result['data'])

    def test_save_then_reload_and_overwrite(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            request = self._request(self.admin)
            saved = analytics_api.save_client_report_draft(
                request, 7, ClientReportDraftSaveRequest(data={'title': 'Q1', 'status': 'draft'}),
            )
            self.assertEqual(saved['data']['title'], 'Q1')
            self.assertEqual(saved['updated_by_name'], self.admin.full_name)

            analytics_api.save_client_report_draft(
                request, 7, ClientReportDraftSaveRequest(data={'title': 'Q1 final', 'status': 'completed'}),
            )
            loaded = analytics_api.get_client_report_draft(request, 7)
            self.assertEqual(loaded['data']['title'], 'Q1 final')
            self.assertEqual(loaded['status'], 'completed')

    def test_drafts_are_per_client(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            request = self._request(self.admin)
            analytics_api.save_client_report_draft(request, 1, ClientReportDraftSaveRequest(data={'title': 'A'}))
            analytics_api.save_client_report_draft(request, 2, ClientReportDraftSaveRequest(data={'title': 'B'}))
            self.assertEqual(analytics_api.get_client_report_draft(request, 1)['data']['title'], 'A')
            self.assertEqual(analytics_api.get_client_report_draft(request, 2)['data']['title'], 'B')

    def test_staff_without_report_permission_cannot_save(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with self.assertRaises(HttpError) as ctx:
                analytics_api.save_client_report_draft(
                    self._request(self.staff), 7, ClientReportDraftSaveRequest(data={'title': 'x'}),
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_oversized_report_is_rejected(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch.object(analytics_api, '_MAX_REPORT_DRAFT_BYTES', 10):
                with self.assertRaises(HttpError) as ctx:
                    analytics_api.save_client_report_draft(
                        self._request(self.admin), 7, ClientReportDraftSaveRequest(data={'title': 'too long for the cap'}),
                    )
        self.assertEqual(ctx.exception.status_code, 413)
