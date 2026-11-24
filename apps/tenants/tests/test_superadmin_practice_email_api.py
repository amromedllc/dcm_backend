from django.test import TestCase, override_settings
from django_tenants.utils import schema_context
from unittest.mock import patch
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.accounts.models import PasswordSetLink
from apps.clients.models import Client
from apps.tenants.api import (
    create_superadmin_organization,
    create_superadmin_tpms_admin_id,
    list_superadmin_practice_email_settings,
    update_superadmin_organization,
    update_superadmin_practice_email_setting,
)
from apps.tenants.models import Domain, Organization, OrganizationTpmsAdminId
from apps.tenants.schemas import (
    OrganizationSuperadminCreate,
    OrganizationSuperadminUpdate,
    TpmsAdminEmailSettingUpdate,
    TpmsAdminIdCreate,
)


class FakeRequest:
    def __init__(self, user):
        self.user = user


class SuperadminPracticeEmailApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Sales Org',
            slug='sales-org',
            schema_name='sales_org',
        )
        self.mapping = OrganizationTpmsAdminId.objects.create(
            organization=self.org,
            admin_id=188,
            facility_name='Main Facility',
        )
        self.superadmin = User.objects.create_user(
            email='super@example.com',
            password='x',
            first_name='Super',
            last_name='Admin',
            role=User.Role.ADMIN,
            is_superuser=True,
        )
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='x',
            first_name='Org',
            last_name='Admin',
            role=User.Role.ADMIN,
            organization=self.org,
        )

    def test_superadmin_can_list_practice_email_settings(self):
        rows = list_superadmin_practice_email_settings(FakeRequest(self.superadmin))

        self.assertEqual(rows[0]['name'], 'Sales Org')
        self.assertEqual(rows[0]['tpms_admin_ids'][0]['admin_id'], 188)
        self.assertEqual(rows[0]['tpms_admin_ids'][0]['facility_name'], 'Main Facility')
        self.assertIs(rows[0]['tpms_admin_ids'][0]['email_notifications_enabled'], False)

    def test_superadmin_can_update_practice_email_setting(self):
        result = update_superadmin_practice_email_setting(
            FakeRequest(self.superadmin),
            self.mapping.id,
            TpmsAdminEmailSettingUpdate(
                facility_name='Updated Facility',
                email_notifications_enabled=True,
            ),
        )

        self.mapping.refresh_from_db()
        self.assertEqual(result['facility_name'], 'Updated Facility')
        self.assertEqual(self.mapping.facility_name, 'Updated Facility')
        self.assertIs(result['email_notifications_enabled'], True)
        self.assertIs(self.mapping.email_notifications_enabled, True)

    def test_facilities_default_to_ai_disabled(self):
        rows = list_superadmin_practice_email_settings(FakeRequest(self.superadmin))
        self.assertIs(rows[0]['tpms_admin_ids'][0]['ai_enabled'], False)

    def test_superadmin_can_toggle_ai_for_a_facility(self):
        result = update_superadmin_practice_email_setting(
            FakeRequest(self.superadmin),
            self.mapping.id,
            TpmsAdminEmailSettingUpdate(ai_enabled=True),
        )
        self.mapping.refresh_from_db()
        self.assertIs(result['ai_enabled'], True)
        self.assertIs(self.mapping.ai_enabled, True)
        # Email flag is untouched by an AI-only update.
        self.assertIs(self.mapping.email_notifications_enabled, False)

        result = update_superadmin_practice_email_setting(
            FakeRequest(self.superadmin),
            self.mapping.id,
            TpmsAdminEmailSettingUpdate(ai_enabled=False),
        )
        self.mapping.refresh_from_db()
        self.assertIs(result['ai_enabled'], False)
        self.assertIs(self.mapping.ai_enabled, False)

    def test_regular_admin_cannot_toggle_ai_for_a_facility(self):
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_practice_email_setting(
                FakeRequest(self.admin),
                self.mapping.id,
                TpmsAdminEmailSettingUpdate(ai_enabled=True),
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.mapping.refresh_from_db()
        self.assertIs(self.mapping.ai_enabled, False)

    def test_superadmin_can_create_facility_with_ai_enabled(self):
        status, created = create_superadmin_tpms_admin_id(
            FakeRequest(self.superadmin),
            TpmsAdminIdCreate(organization_id=self.org.id, admin_id=555, facility_name='AI Clinic', ai_enabled=True),
        )
        self.assertEqual(status, 201)
        self.assertIs(created['ai_enabled'], True)

    def test_regular_admin_cannot_update_practice_email_setting(self):
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_practice_email_setting(
                FakeRequest(self.admin),
                self.mapping.id,
                TpmsAdminEmailSettingUpdate(email_notifications_enabled=True),
            )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_superadmin_can_create_organization_with_tpms_admin_ids(self):
        result_status, result = create_superadmin_organization(
            FakeRequest(self.superadmin),
            OrganizationSuperadminCreate(
                name='New Sales Org',
                slug='new-sales-org',
                schema_name='new_sales_org',
                domain='new-sales-org.localhost',
                plan='professional',
                tpms_admin_ids=[
                    {'admin_id': 991, 'facility_name': 'North Facility'},
                    {'admin_id': 992, 'facility_name': 'South Facility'},
                ],
            ),
        )

        self.assertEqual(result_status, 201)
        self.assertEqual(result['schema_name'], 'new_sales_org')
        self.assertEqual(result['domain'], 'new-sales-org.localhost')
        self.assertEqual([row['admin_id'] for row in result['tpms_admin_ids']], [991, 992])
        self.assertEqual(result['tpms_admin_ids'][0]['facility_name'], 'North Facility')

    @override_settings(FRONTEND_BASE_URL='http://app.example.test:3000', SIGNUP_SALES_NOTIFICATION_EMAIL='')
    @patch('apps.tenants.api.EmailMultiAlternatives')
    def test_free_trial_creation_can_seed_demo_data_and_send_setup_email(self, email_class):
        # Superadmin requests can be routed through an unrelated tenant schema.
        with schema_context(self.org.schema_name):
            status, result = create_superadmin_organization(
                FakeRequest(self.superadmin),
                OrganizationSuperadminCreate(
                    name='Trial Demo Clinic',
                    slug='trial-demo-clinic',
                    plan=Organization.Plan.FREE_TRIAL,
                    seed_demo_data=True,
                    admin_first_name='Taylor',
                    admin_last_name='Admin',
                    admin_email='taylor@example.test',
                ),
            )

        self.assertEqual(status, 201)
        self.assertEqual(result['domain'], 'app.example.test')
        org = Organization.objects.get(pk=result['id'])
        admin = User.objects.get(email='taylor@example.test')
        self.assertEqual(admin.organization_id, org.id)
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertFalse(admin.has_usable_password())
        self.assertTrue(PasswordSetLink.objects.filter(user=admin, created_by=self.superadmin).exists())
        with schema_context(org.schema_name):
            self.assertEqual(Client.all_organizations.filter(organization_id=org.id, created_by=admin).count(), 1)
        self.assertGreater(org.trial_ends_at, org.created_at)
        email_class.assert_called_once()
        self.assertIn('free trial', email_class.call_args.kwargs['subject'].lower())
        self.assertEqual(email_class.call_args.kwargs['to'], ['taylor@example.test'])
        self.assertIn('http://app.example.test:3000/set-password?token=', email_class.call_args.kwargs['body'])
        email_class.return_value.send.assert_called_once_with(fail_silently=False)

    def test_demo_onboarding_requires_admin_contact_and_free_trial_plan(self):
        with self.assertRaises(HttpError) as missing_contact:
            create_superadmin_organization(
                FakeRequest(self.superadmin),
                OrganizationSuperadminCreate(
                    name='Missing Contact Clinic', slug='missing-contact-clinic', seed_demo_data=True,
                ),
            )
        self.assertEqual(missing_contact.exception.status_code, 400)

        with self.assertRaises(HttpError) as wrong_plan:
            create_superadmin_organization(
                FakeRequest(self.superadmin),
                OrganizationSuperadminCreate(
                    name='Starter Demo Clinic', slug='starter-demo-clinic', plan='starter',
                    seed_demo_data=True, admin_first_name='Taylor', admin_last_name='Admin',
                    admin_email='taylor@example.test',
                ),
            )
        self.assertEqual(wrong_plan.exception.status_code, 400)

    def test_superadmin_can_update_organization_and_add_tpms_admin_id(self):
        updated = update_superadmin_organization(
            FakeRequest(self.superadmin),
            self.org.id,
            OrganizationSuperadminUpdate(
                name='Renamed Org',
                domain='renamed.localhost',
                is_active=False,
            ),
        )
        created_status, created = create_superadmin_tpms_admin_id(
            FakeRequest(self.superadmin),
            TpmsAdminIdCreate(
                organization_id=self.org.id,
                admin_id=777,
                facility_name='Evening Clinic',
                email_notifications_enabled=True,
            ),
        )

        self.org.refresh_from_db()
        self.assertEqual(updated['name'], 'Renamed Org')
        self.assertFalse(self.org.is_active)
        self.assertEqual(created_status, 201)
        self.assertEqual(created['admin_id'], 777)
        self.assertEqual(created['facility_name'], 'Evening Clinic')
        self.assertTrue(created['email_notifications_enabled'])

    @override_settings(FRONTEND_BASE_URL='https://app.example.test')
    def test_clearing_domain_uses_configured_app_host(self):
        updated = update_superadmin_organization(
            FakeRequest(self.superadmin), self.org.id, OrganizationSuperadminUpdate(domain=''),
        )
        self.assertEqual(updated['domain'], 'app.example.test')
        self.assertEqual(
            Domain.objects.get(tenant=self.org, is_primary=True).domain,
            'app.example.test',
        )
