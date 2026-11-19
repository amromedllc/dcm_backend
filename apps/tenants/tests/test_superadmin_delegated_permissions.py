from django.test import TestCase
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.tenants.api import (
    create_superadmin_organization,
    create_superadmin_user,
    deactivate_superadmin_user,
    list_superadmin_users,
    update_superadmin_user,
    update_superadmin_user_delegated_permissions,
)
from apps.tenants.models import Organization
from apps.tenants.schemas import (
    OrganizationSuperadminCreate,
    SuperadminDelegatedPermissionsUpdate,
    SuperadminUserCreate,
    SuperadminUserUpdate,
)


class FakeRequest:
    def __init__(self, user):
        self.user = user


class SuperadminDelegatedPermissionsTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email='super@example.com',
            password='x',
            first_name='Super',
            last_name='Admin',
            role=User.Role.ADMIN,
            is_superuser=True,
        )
        self.sales = User.objects.create_user(
            email='sales@example.com',
            password='x',
            first_name='Sales',
            last_name='Rep',
            role=User.Role.SALES,
        )

    def test_regular_user_cannot_create_organization(self):
        with self.assertRaises(HttpError) as ctx:
            create_superadmin_organization(
                FakeRequest(self.sales),
                OrganizationSuperadminCreate(
                    name='Blocked Org', slug='blocked-org', schema_name='blocked_org',
                    domain='blocked-org.localhost', plan='professional',
                ),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_superadmin_can_grant_manage_organizations_and_sales_user_can_then_create(self):
        update_superadmin_user_delegated_permissions(
            FakeRequest(self.superadmin),
            self.sales.id,
            SuperadminDelegatedPermissionsUpdate(permissions={'manage_organizations': True}),
        )
        self.sales.refresh_from_db()
        self.assertTrue(self.sales.superadmin_delegated_permissions['manage_organizations'])

        status, result = create_superadmin_organization(
            FakeRequest(self.sales),
            OrganizationSuperadminCreate(
                name='Delegated Org', slug='delegated-org', schema_name='delegated_org',
                domain='delegated-org.localhost', plan='professional',
            ),
        )
        self.assertEqual(status, 201)
        self.assertEqual(result['schema_name'], 'delegated_org')

    def test_grant_is_scoped_to_the_specific_permission(self):
        update_superadmin_user_delegated_permissions(
            FakeRequest(self.superadmin),
            self.sales.id,
            SuperadminDelegatedPermissionsUpdate(permissions={'manage_changelog': True}),
        )
        self.sales.refresh_from_db()
        with self.assertRaises(HttpError) as ctx:
            create_superadmin_organization(
                FakeRequest(self.sales),
                OrganizationSuperadminCreate(
                    name='Still Blocked', slug='still-blocked', schema_name='still_blocked',
                    domain='still-blocked.localhost', plan='professional',
                ),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_non_superuser_cannot_grant_permissions(self):
        other = User.objects.create_user(
            email='other@example.com', password='x', first_name='Other', last_name='User',
            role=User.Role.STAFF,
        )
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_user_delegated_permissions(
                FakeRequest(self.sales),
                other.id,
                SuperadminDelegatedPermissionsUpdate(permissions={'manage_organizations': True}),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_unknown_permission_key_is_rejected(self):
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_user_delegated_permissions(
                FakeRequest(self.superadmin),
                self.sales.id,
                SuperadminDelegatedPermissionsUpdate(permissions={'is_superuser': True}),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_cannot_grant_to_an_existing_superuser(self):
        other_superadmin = User.objects.create_user(
            email='super2@example.com', password='x', first_name='Super', last_name='Two',
            role=User.Role.ADMIN, is_superuser=True,
        )
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_user_delegated_permissions(
                FakeRequest(self.superadmin),
                other_superadmin.id,
                SuperadminDelegatedPermissionsUpdate(permissions={'manage_organizations': True}),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_cannot_grant_to_a_non_sales_role_user(self):
        staff = User.objects.create_user(
            email='staff-only@example.com', password='x', first_name='Staff', last_name='Only',
            role=User.Role.STAFF,
        )
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_user_delegated_permissions(
                FakeRequest(self.superadmin),
                staff.id,
                SuperadminDelegatedPermissionsUpdate(permissions={'manage_organizations': True}),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_stale_grant_is_ignored_after_role_is_changed_away_from_sales(self):
        update_superadmin_user_delegated_permissions(
            FakeRequest(self.superadmin),
            self.sales.id,
            SuperadminDelegatedPermissionsUpdate(permissions={'manage_organizations': True}),
        )
        self.sales.role = User.Role.STAFF
        self.sales.save(update_fields=['role'])

        with self.assertRaises(HttpError) as ctx:
            create_superadmin_organization(
                FakeRequest(self.sales),
                OrganizationSuperadminCreate(
                    name='No Longer Sales', slug='no-longer-sales', schema_name='no_longer_sales',
                    domain='no-longer-sales.localhost', plan='professional',
                ),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_revoking_a_permission_removes_access(self):
        update_superadmin_user_delegated_permissions(
            FakeRequest(self.superadmin),
            self.sales.id,
            SuperadminDelegatedPermissionsUpdate(permissions={'manage_organizations': True}),
        )
        update_superadmin_user_delegated_permissions(
            FakeRequest(self.superadmin),
            self.sales.id,
            SuperadminDelegatedPermissionsUpdate(permissions={'manage_organizations': False}),
        )
        self.sales.refresh_from_db()
        self.assertFalse(self.sales.superadmin_delegated_permissions['manage_organizations'])
        with self.assertRaises(HttpError) as ctx:
            create_superadmin_organization(
                FakeRequest(self.sales),
                OrganizationSuperadminCreate(
                    name='Revoked Org', slug='revoked-org', schema_name='revoked_org',
                    domain='revoked-org.localhost', plan='professional',
                ),
            )
        self.assertEqual(ctx.exception.status_code, 403)


class SuperadminDelegatedManageUsersTests(TestCase):
    """manage_users lets a delegated sales account administer regular staff
    accounts, but it must never be able to create, edit, or deactivate
    another 'sales' row — only a real superadmin can touch those."""

    def setUp(self):
        self.superadmin = User.objects.create_user(
            email='super-mu@example.com', password='x', first_name='Super', last_name='Admin',
            role=User.Role.ADMIN, is_superuser=True,
        )
        self.org = Organization.objects.create(name='Manage Users Org', slug='mu-org', schema_name='mu_org')
        self.sales_manager = User.objects.create_user(
            email='sales-mu@example.com', password='x', first_name='Sales', last_name='Manager',
            role=User.Role.SALES,
        )
        update_superadmin_user_delegated_permissions(
            FakeRequest(self.superadmin),
            self.sales_manager.id,
            SuperadminDelegatedPermissionsUpdate(permissions={'manage_users': True}),
        )

    def test_delegated_manager_can_create_a_staff_user(self):
        status, result = create_superadmin_user(
            FakeRequest(self.sales_manager),
            SuperadminUserCreate(
                organization_id=self.org.id, email='new-staff@example.com',
                first_name='New', last_name='Staff', password='password123', role='staff',
            ),
        )
        self.assertEqual(status, 201)
        self.assertEqual(result['role'], 'staff')

    def test_delegated_manager_cannot_create_a_sales_user(self):
        with self.assertRaises(HttpError) as ctx:
            create_superadmin_user(
                FakeRequest(self.sales_manager),
                SuperadminUserCreate(
                    organization_id=self.org.id, email='new-sales@example.com',
                    first_name='New', last_name='Sales', password='password123', role='sales',
                ),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_delegated_manager_cannot_edit_an_existing_sales_user(self):
        other_sales = User.objects.create_user(
            email='other-sales@example.com', password='x', first_name='Other', last_name='Sales',
            role=User.Role.SALES,
        )
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_user(
                FakeRequest(self.sales_manager), other_sales.id,
                SuperadminUserUpdate(first_name='Changed'),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_delegated_manager_cannot_promote_a_staff_user_to_sales(self):
        staff = User.objects.create_user(
            email='staff-to-promote@example.com', password='x', first_name='Staff', last_name='ToPromote',
            role=User.Role.STAFF, organization=self.org,
        )
        with self.assertRaises(HttpError) as ctx:
            update_superadmin_user(
                FakeRequest(self.sales_manager), staff.id,
                SuperadminUserUpdate(role='sales'),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_delegated_manager_cannot_deactivate_a_sales_user(self):
        other_sales = User.objects.create_user(
            email='deactivate-sales@example.com', password='x', first_name='Other', last_name='Sales',
            role=User.Role.SALES,
        )
        with self.assertRaises(HttpError) as ctx:
            deactivate_superadmin_user(FakeRequest(self.sales_manager), other_sales.id)
        self.assertEqual(ctx.exception.status_code, 403)
        other_sales.refresh_from_db()
        self.assertTrue(other_sales.is_active)

    def test_sales_rows_are_hidden_from_a_delegated_managers_user_list(self):
        User.objects.create_user(
            email='hidden-sales@example.com', password='x', first_name='Hidden', last_name='Sales',
            role=User.Role.SALES,
        )
        results = list_superadmin_users(FakeRequest(self.sales_manager))
        self.assertNotIn('sales', [u['role'] for u in results])

    def test_real_superadmin_can_still_manage_sales_rows(self):
        status, result = create_superadmin_user(
            FakeRequest(self.superadmin),
            SuperadminUserCreate(
                organization_id=self.org.id, email='admin-made-sales@example.com',
                first_name='Admin', last_name='MadeSales', password='password123', role='sales',
            ),
        )
        self.assertEqual(status, 201)
        self.assertEqual(result['role'], 'sales')
