from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts.api import get_features
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from shared import ai_client


class AiFlagTests(SimpleTestCase):
    @override_settings(AI_ENABLED=False, AI_API_KEY='k')
    def test_off_flag_disables_ai_even_with_key(self):
        self.assertFalse(ai_client.is_enabled())
        with self.assertRaises(ai_client.AIError) as ctx:
            ai_client.chat_json('s', 'u')
        self.assertEqual(ctx.exception.status, 403)

    @override_settings(AI_ENABLED=True, AI_API_KEY='')
    def test_on_flag_without_key_is_not_enabled(self):
        self.assertFalse(ai_client.is_enabled())

    @override_settings(AI_ENABLED=True, AI_API_KEY='k')
    def test_on_flag_with_key_is_enabled(self):
        self.assertTrue(ai_client.is_enabled())


@override_settings(AI_ENABLED=True, AI_API_KEY='k')
class FacilityAiFlagTests(TestCase):
    """AI must be switched on per TPMS facility (admin ID) by a superadmin, on top of the deployment switch."""

    def setUp(self):
        self.org = Organization.objects.create(name='Org', slug='org', schema_name='org')
        self.other_org = Organization.objects.create(name='Other', slug='other', schema_name='other')
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=188, facility_name='AI Clinic', ai_enabled=True)
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=1333, facility_name='Plain Clinic')
        self.user_on = SimpleNamespace(organization_id=self.org.id, external_admin_id=188)
        self.user_off = SimpleNamespace(organization_id=self.org.id, external_admin_id=1333)
        self.user_unmapped = SimpleNamespace(organization_id=self.org.id, external_admin_id=999)
        self.user_wrong_org = SimpleNamespace(organization_id=self.other_org.id, external_admin_id=188)
        self.user_native = SimpleNamespace(organization_id=self.org.id, external_admin_id=None)

    def test_is_enabled_respects_facility_flag(self):
        self.assertTrue(ai_client.is_enabled(self.user_on))
        self.assertFalse(ai_client.is_enabled(self.user_off))
        self.assertFalse(ai_client.is_enabled(self.user_unmapped))

    def test_facility_flag_does_not_leak_across_organizations(self):
        self.assertFalse(ai_client.is_enabled(self.user_wrong_org))

    def test_user_without_facility_is_gated_only_by_deployment_switch(self):
        self.assertTrue(ai_client.is_enabled(self.user_native))

    @override_settings(AI_ENABLED=False)
    def test_deployment_switch_still_wins_over_facility_flag(self):
        self.assertFalse(ai_client.is_enabled(self.user_on))

    def test_features_endpoint_reports_facility_flag(self):
        self.assertTrue(get_features(SimpleNamespace(user=self.user_on))['ai_enabled'])
        self.assertFalse(get_features(SimpleNamespace(user=self.user_off))['ai_enabled'])

    def test_ensure_enabled_refuses_facility_with_ai_off(self):
        ai_client.ensure_enabled_for_user(self.user_on)
        with self.assertRaises(ai_client.AIError) as ctx:
            ai_client.ensure_enabled_for_user(self.user_off)
        self.assertEqual(ctx.exception.status, 403)
