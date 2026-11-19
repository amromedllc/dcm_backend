"""
A non-superuser account holding a superadmin_delegated_permissions grant
(e.g. a sales-team login scoped to Knowledge Base / Changelog / Organization
creation) must be able to log in from any tenant's hostname without
belonging to that organization — the same bypass real superusers already
get in _platform_local_auth (formerly _superadmin_local_auth). A plain
staff/admin account without a grant must still be rejected for a tenant it
doesn't belong to.
"""
from django.test import TestCase
from ninja.errors import HttpError

from apps.accounts.api import login
from apps.accounts.auth import JWTAuth
from apps.accounts.models import User
from apps.accounts.permissions import require_superadmin_or_delegated
from apps.accounts.schemas import LoginRequest
from apps.tenants.models import Organization


class FakeRequest:
    def __init__(self, tenant):
        self.tenant = tenant


class DelegatedPlatformLoginTests(TestCase):
    def setUp(self):
        self.other_org = Organization.objects.create(
            name='Other Org', slug='other-org', schema_name='other_org',
        )
        self.login_tenant = Organization.objects.create(
            name='Login Tenant', slug='login-tenant', schema_name='login_tenant',
        )

    def test_delegated_user_can_log_in_to_an_org_they_do_not_belong_to(self):
        sales = User.objects.create_user(
            email='sales@example.com', password='correct-password',
            first_name='Sales', last_name='Rep', role=User.Role.SALES,
            organization=self.other_org,
        )
        sales.superadmin_delegated_permissions = {'manage_changelog': True}
        sales.save(update_fields=['superadmin_delegated_permissions'])

        response = login(FakeRequest(self.login_tenant), LoginRequest(email=sales.email, password='correct-password'))
        self.assertTrue(response.access_token)

        sales.refresh_from_db()
        self.assertEqual(sales.organization_id, self.other_org.id)

    def test_plain_staff_user_without_a_grant_cannot_log_in_outside_their_org(self):
        staff = User.objects.create_user(
            email='staff@example.com', password='correct-password',
            first_name='Plain', last_name='Staff', role=User.Role.STAFF,
            organization=self.other_org,
        )
        with self.assertRaises(HttpError) as ctx:
            login(FakeRequest(self.login_tenant), LoginRequest(email=staff.email, password='correct-password'))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_delegated_user_with_all_permissions_false_still_rejected_outside_org(self):
        sales = User.objects.create_user(
            email='nogrant@example.com', password='correct-password',
            first_name='No', last_name='Grant', role=User.Role.SALES,
            organization=self.other_org,
        )
        sales.superadmin_delegated_permissions = {'manage_changelog': False, 'manage_knowledge_base': False}
        sales.save(update_fields=['superadmin_delegated_permissions'])

        with self.assertRaises(HttpError) as ctx:
            login(FakeRequest(self.login_tenant), LoginRequest(email=sales.email, password='correct-password'))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_token_from_a_delegated_login_actually_authenticates_on_later_requests(self):
        """Regression: JWTAuth.allowed_roles previously excluded 'sales', so a
        sales user could log in successfully but then get a 401 on every
        subsequent request (including the very endpoints their grant was
        meant to unlock) — which is what made the web app immediately log
        them back out after a seemingly successful sign-in."""
        sales = User.objects.create_user(
            email='sales3@example.com', password='correct-password',
            first_name='Sales', last_name='Three', role=User.Role.SALES,
            organization=self.other_org,
        )
        sales.superadmin_delegated_permissions = {'manage_changelog': True}
        sales.save(update_fields=['superadmin_delegated_permissions'])

        tokens = login(FakeRequest(self.login_tenant), LoginRequest(email=sales.email, password='correct-password'))

        request = FakeRequest(self.login_tenant)
        authenticated_user = JWTAuth().authenticate(request, tokens.access_token)
        self.assertIsNotNone(authenticated_user)
        self.assertEqual(authenticated_user.id, sales.id)

        # And the actual permission check behind the delegated endpoints passes too.
        request.user = authenticated_user
        require_superadmin_or_delegated(request, 'manage_changelog')  # must not raise

    def test_wrong_password_is_still_rejected_for_a_delegated_user(self):
        sales = User.objects.create_user(
            email='sales2@example.com', password='correct-password',
            first_name='Sales', last_name='Two', role=User.Role.SALES,
            organization=self.other_org,
        )
        sales.superadmin_delegated_permissions = {'manage_organizations': True}
        sales.save(update_fields=['superadmin_delegated_permissions'])

        with self.assertRaises(HttpError) as ctx:
            login(FakeRequest(self.login_tenant), LoginRequest(email=sales.email, password='wrong-password'))
        self.assertEqual(ctx.exception.status_code, 401)
