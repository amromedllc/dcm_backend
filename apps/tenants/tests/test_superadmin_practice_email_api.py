from django.test import TestCase
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.tenants.api import (
    create_superadmin_organization,
    create_superadmin_tpms_admin_id,
    list_superadmin_practice_email_settings,
    update_superadmin_organization,
    update_superadmin_practice_email_setting,
)
from apps.tenants.models import Organization, OrganizationTpmsAdminId
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
