"""Session recording endpoints enforce the session_start privilege."""
import inspect
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import RolePermission, User
from apps.sessions import api as sessions_api
from apps.sessions.models import SessionRun
from apps.sessions.schemas import SessionStartRequest
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

RECORDING_ENDPOINTS = [
    'start_session', 'delete_session', 'add_trial', 'delete_trial', 'add_behavior',
    'delete_behavior', 'add_abc', 'delete_abc', 'submit', 'sync_session', 'upload_session_media',
]


class SessionStartPermissionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Perm Org', slug='perm-org', schema_name='perm_org')
        self.staff = User.objects.create_user(
            email='perm-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        self.admin = User.objects.create_user(
            email='perm-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def _revoke(self, role):
        RolePermission.objects.update_or_create(
            organization=self.org, role=role, defaults={'permissions': {'session_start': False}},
        )

    def test_staff_has_it_by_default(self):
        sessions_api._require_session_start(self._request(self.staff))

    def test_every_recording_endpoint_refuses_a_role_without_session_start(self):
        self._revoke(User.Role.STAFF)
        request = self._request(self.staff)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk), \
                mock.patch.object(sessions_api, '_accessible_external_client_ids', return_value={1}):
            live = SessionRun.objects.create(external_client_id=1, staff=self.staff)
            for name in RECORDING_ENDPOINTS:
                with self.subTest(endpoint=name):
                    endpoint = getattr(sessions_api, name)
                    args = []
                    for parameter in list(inspect.signature(endpoint).parameters)[1:]:
                        if parameter == 'session_id':
                            args.append(live.id)
                        elif parameter == 'data' and name == 'start_session':
                            args.append(SessionStartRequest(client_id=1))
                        else:
                            args.append(mock.MagicMock())
                    with self.assertRaises(HttpError) as ctx:
                        endpoint(request, *args)
                    self.assertEqual(ctx.exception.status_code, 403)

    def test_revoking_only_affects_that_role(self):
        self._revoke(User.Role.STAFF)
        sessions_api._require_session_start(self._request(self.admin))

    def test_admin_cannot_lose_it(self):
        self._revoke(User.Role.ADMIN)
        sessions_api._require_session_start(self._request(self.admin))
