"""
Caregiver/client-portal login: the login-dispatch fix and the
role-partitioned JWT auth that makes it structural rather than a one-off
patch. See project memory / plan notes for the pre-existing bug this
closes — a TPMS client-portal (account_type='Client') login previously got
its patient id misread as an employee id and silently bound to the
practice with role='staff'.
"""
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.api import login
from apps.accounts.auth import JWTAuth, CaregiverJWTAuth, create_access_token
from apps.accounts.models import User
from apps.accounts.schemas import LoginRequest
from apps.clients.models import Client
from apps.integrations.tpms_auth_client import account_type_of, normalize_client_portal_payload, TpmsAuthError
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from shared.tenancy import tenant_context


# Real payloads captured against production TherapyPMS this session.
STAFF_LOGIN_PAYLOAD = {
    'status': 'success', 'account_type': 'Provider', 'message': 'Provider logged in',
    'access_token': 'Bearer staff.jwt.token', 'admin_id': 501,
    'first_name': 'Jane', 'last_name': 'Smith', 'email': 'jane@example.com',
    'employee_type': 'RBT', 'is_admin': False,
}

CLIENT_PORTAL_LOGIN_PAYLOAD = {
    'status': 'success', 'account_type': 'Client', 'message': 'Client successfully logged in',
    'access_token': 'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.client.jwt.token',
    'token_type': 'Bearer', 'has_sibling': True,
    'user': {'id': 13086, 'client_full_name': 'Antony  R', 'email': 'anto@amromed.org'},
    'tabs': {'main_tabs': ['My Schedule', 'My Calendar', 'My Info', 'My Invoice'],
             'my_info_tabs': ['Patient Info', 'Documents']},
}


class FakeRequest:
    def __init__(self, tenant):
        self.tenant = tenant


class TpmsPayloadNormalizationTests(TestCase):
    """Pure-function tests, no DB — the direct regression test for the
    original bug: a client-portal payload must never resolve as staff."""

    def test_account_type_of_client_portal(self):
        self.assertEqual(account_type_of(CLIENT_PORTAL_LOGIN_PAYLOAD), 'client')

    def test_account_type_of_staff(self):
        self.assertEqual(account_type_of(STAFF_LOGIN_PAYLOAD), 'provider')

    def test_normalize_client_portal_payload_extracts_patient_id_not_employee_id(self):
        profile = normalize_client_portal_payload('fallback@example.com', CLIENT_PORTAL_LOGIN_PAYLOAD)
        self.assertEqual(profile.external_client_id, 13086)
        self.assertEqual(profile.email, 'anto@amromed.org')
        # double-space in "Antony  R" must not produce an empty last_name
        self.assertEqual(profile.first_name, 'Antony')
        self.assertEqual(profile.last_name, 'R')
        self.assertTrue(profile.has_sibling)
        self.assertFalse(hasattr(profile, 'external_admin_id'))
        self.assertFalse(hasattr(profile, 'external_employee_id'))

    def test_normalize_client_portal_payload_missing_id_raises(self):
        with self.assertRaises(TpmsAuthError):
            normalize_client_portal_payload('fallback@example.com', {'account_type': 'Client', 'user': {}})


class LoginDispatchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org Caregiver', slug='test-org-caregiver', schema_name='test_org_caregiver',
        )
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=501)
        self.request = FakeRequest(tenant=self.org)

    def _login(self, payload):
        with patch('apps.accounts.api.tpms_authenticate_raw', return_value=payload):
            return login(self.request, LoginRequest(email='someone@example.com', password='x'))

    def test_client_portal_login_never_yields_staff_role(self):
        """Core regression test for the pre-existing bug."""
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Antony', last_name='R', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            tokens = self._login(CLIENT_PORTAL_LOGIN_PAYLOAD)
            self.assertEqual(tokens.role, User.Role.CAREGIVER)
            user = User.objects.get(id=tokens.user_id)
            self.assertEqual(user.role, User.Role.CAREGIVER)
            self.assertEqual(user.external_client_id, 13086)
            self.assertIsNone(user.external_employee_id)
            self.assertEqual(user.external_admin_id, 501)

    def test_staff_login_still_works(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            tokens = self._login(STAFF_LOGIN_PAYLOAD)
            self.assertEqual(tokens.role, User.Role.STAFF)
            user = User.objects.get(id=tokens.user_id)
            self.assertIsNone(user.external_client_id)

    def test_client_portal_login_without_local_client_row_is_403(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with self.assertRaises(HttpError) as ctx:
                self._login(CLIENT_PORTAL_LOGIN_PAYLOAD)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_client_portal_login_wrong_practice_is_401(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Antony', last_name='R', external_id='13086',
                external_admin_id=999,  # not in this org's tpms_admin_ids
                organization=self.org,
            )
            with self.assertRaises(HttpError) as ctx:
                self._login(CLIENT_PORTAL_LOGIN_PAYLOAD)
            self.assertEqual(ctx.exception.status_code, 401)

    def test_unusual_but_non_client_account_type_still_logs_in_as_staff(self):
        """Staff is the default path, not an allowlisted one — TPMS's real
        account_type values for staff/admin logins vary more than any
        hardcoded set could reliably enumerate (this is the exact bug that
        broke real staff logins during manual testing of this feature)."""
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            tokens = self._login({**STAFF_LOGIN_PAYLOAD, 'account_type': 'something_unforeseen'})
            self.assertEqual(tokens.role, User.Role.STAFF)

    def test_staff_login_blocked_if_email_already_caregiver(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Antony', last_name='R', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            self._login(CLIENT_PORTAL_LOGIN_PAYLOAD)  # provisions the caregiver row
            staff_payload = {**STAFF_LOGIN_PAYLOAD, 'email': 'anto@amromed.org'}
            with self.assertRaises(HttpError) as ctx:
                self._login(staff_payload)
            self.assertEqual(ctx.exception.status_code, 409)

    def test_caregiver_login_blocked_if_email_already_staff(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Antony', last_name='R', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            staff_payload = {**STAFF_LOGIN_PAYLOAD, 'email': 'anto@amromed.org'}
            self._login(staff_payload)  # provisions the staff row
            with self.assertRaises(HttpError) as ctx:
                self._login(CLIENT_PORTAL_LOGIN_PAYLOAD)
            self.assertEqual(ctx.exception.status_code, 409)


class UserModelConstraintTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org Constraint', slug='test-org-constraint', schema_name='test_org_constraint',
        )

    def test_staff_role_with_client_link_rejected(self):
        with self.assertRaises(IntegrityError):
            User.objects.create(
                email='bad@example.com', role=User.Role.STAFF, external_client_id=123,
                organization=self.org,
            )

    def test_caregiver_role_without_client_link_rejected(self):
        with self.assertRaises(IntegrityError):
            User.objects.create(
                email='bad2@example.com', role=User.Role.CAREGIVER, external_client_id=None,
                organization=self.org,
            )


class RolePartitionedAuthTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org Auth', slug='test-org-auth', schema_name='test_org_auth',
        )
        self.staff = User.objects.create(
            email='staff@example.com', role=User.Role.STAFF, organization=self.org,
        )
        self.caregiver = User.objects.create(
            email='caregiver@example.com', role=User.Role.CAREGIVER,
            external_client_id=13086, organization=self.org,
        )

    def _request(self):
        return FakeRequest(tenant=self.org)

    def test_staff_jwt_rejected_by_caregiver_auth(self):
        token = create_access_token(self.staff, self.org.pk)
        self.assertIsNone(CaregiverJWTAuth().authenticate(self._request(), token))

    def test_caregiver_jwt_rejected_by_default_jwt_auth(self):
        token = create_access_token(self.caregiver, self.org.pk)
        self.assertIsNone(JWTAuth().authenticate(self._request(), token))

    def test_caregiver_jwt_accepted_by_caregiver_auth(self):
        token = create_access_token(self.caregiver, self.org.pk)
        user = CaregiverJWTAuth().authenticate(self._request(), token)
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.caregiver.id)

    def test_staff_jwt_accepted_by_default_jwt_auth(self):
        token = create_access_token(self.staff, self.org.pk)
        user = JWTAuth().authenticate(self._request(), token)
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.staff.id)
