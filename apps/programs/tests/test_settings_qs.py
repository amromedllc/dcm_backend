"""
_settings_qs — practice-scoped queryset for shared facility settings
(TreatmentArea/ProgramTag/TargetStatus/etc). Regression coverage for a bug
where org-default rows (created_by=NULL, copied at org-creation time from
platform DefaultTargetStatus templates — see apps.tenants.services) were
invisible to every practice, because the practice filter joins through
created_by and a NULL join never matches.
"""
from types import SimpleNamespace

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.programs.api import _settings_qs
from apps.programs.models import TargetStatus
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


def _request_for(user):
    return SimpleNamespace(user=user)


class SettingsQsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-settings-qs', schema_name='test_org_settings_qs',
        )
        # Both TPMS-linked, to different practice ids sharing this org's schema —
        # external_admin_id (not organization_id) is what distinguishes them,
        # since one Organization can front several TPMS practices at once.
        self.user = User.objects.create_user(
            email='staff@example.com', password='x',
            first_name='Staff', last_name='User', organization=self.org, role=User.Role.STAFF,
            external_admin_id=101,
        )
        self.other_practice_user = User.objects.create_user(
            email='other-practice@example.com', password='x',
            first_name='Other', last_name='Practice', organization=self.org, role=User.Role.STAFF,
            external_admin_id=999,
        )

    def test_org_default_rows_with_no_author_are_visible(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            TargetStatus.objects.create(key='waiting', label='Waiting', created_by=None)

            keys = set(_settings_qs(TargetStatus, _request_for(self.user)).values_list('key', flat=True))
            self.assertEqual(keys, {'waiting'})

    def test_rows_authored_by_another_practice_stay_excluded(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            TargetStatus.objects.create(key='waiting', label='Waiting', created_by=None)
            TargetStatus.objects.create(key='custom', label='Custom', created_by=self.other_practice_user)

            keys = set(_settings_qs(TargetStatus, _request_for(self.user)).values_list('key', flat=True))
            self.assertEqual(keys, {'waiting'})  # 'custom' (another practice's row) stays hidden

    def test_rows_authored_by_own_practice_are_visible(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            TargetStatus.objects.create(key='mine', label='Mine', created_by=self.user)

            keys = set(_settings_qs(TargetStatus, _request_for(self.user)).values_list('key', flat=True))
            self.assertEqual(keys, {'mine'})
