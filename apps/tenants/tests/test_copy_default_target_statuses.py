"""
copy_default_target_statuses_to_org — copies the platform's
DefaultTargetStatus templates (authored via Django Admin, superuser-only)
into a new org's own programs.TargetStatus table at creation time.
"""
from django.test import TestCase
from django_tenants.utils import schema_context

from apps.programs.models import TargetStatus
from apps.tenants.models import DefaultTargetStatus, Organization
from apps.tenants.services import copy_default_target_statuses_to_org
from shared.tenancy import tenant_context


class CopyDefaultTargetStatusesTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-copy-defaults', schema_name='test_org_copy_defaults',
        )
        # migration 0004_defaulttargetstatus already seeds the platform's 7
        # built-in templates — add one more so this test isn't just re-testing
        # that seed.
        DefaultTargetStatus.objects.create(
            key='vip', label='VIP', color='#ff00ff', icon='star',
            is_staff_visible=True, is_default=False, display_order=99,
        )

    def test_copies_every_platform_template_into_the_org(self):
        with schema_context(self.org.schema_name):
            copy_default_target_statuses_to_org(self.org.id)

            with tenant_context(self.org.pk):
                org_keys = set(TargetStatus.objects.values_list('key', flat=True))
                template_keys = set(DefaultTargetStatus.objects.values_list('key', flat=True))
                self.assertEqual(org_keys, template_keys)
                self.assertIn('vip', org_keys)

                vip = TargetStatus.objects.get(key='vip')
                self.assertEqual(vip.label, 'VIP')
                self.assertEqual(vip.color, '#ff00ff')
                self.assertIsNone(vip.created_by_id)

    def test_idempotent_does_not_clobber_org_edits(self):
        with schema_context(self.org.schema_name):
            copy_default_target_statuses_to_org(self.org.id)
            with tenant_context(self.org.pk):
                TargetStatus.objects.filter(key='vip').update(label='Renamed by org admin')

            copy_default_target_statuses_to_org(self.org.id)  # re-run

            with tenant_context(self.org.pk):
                self.assertEqual(TargetStatus.objects.get(key='vip').label, 'Renamed by org admin')

    def test_platform_templates_are_not_org_scoped(self):
        # DefaultTargetStatus is a SHARED_APPS model — it must be readable
        # without any tenant_context, since it has no organization at all.
        keys = set(DefaultTargetStatus.objects.values_list('key', flat=True))
        self.assertIn('vip', keys)
        self.assertIn('waiting', keys)
