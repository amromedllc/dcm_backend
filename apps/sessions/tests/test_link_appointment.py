from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.clients.models import Client
from apps.sessions.api import link_session_appointment
from apps.sessions.models import Appointment, SessionRun
from apps.sessions.schemas import SessionLinkAppointmentRequest
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class LinkAppointmentTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Link Appointment Org',
            slug='link-appointment-org',
            schema_name='link_appointment_org',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.admin = User.objects.create_user(
            email='link-admin@example.com',
            password='x',
            first_name='Link',
            last_name='Admin',
            organization=self.org,
            role=User.Role.ADMIN,
            external_admin_id=901,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.client_row = Client.objects.create(
                first_name='Link',
                last_name='Client',
                external_id='9901',
                external_admin_id=901,
                organization=self.org,
            )

    def _request(self):
        return SimpleNamespace(user=self.admin)

    def test_link_accepts_local_appointment_client_id_for_canonical_session_client(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            appointment = Appointment.objects.create(
                external_client_id=self.client_row.id,
                external_id='123456',
                start_time=timezone.now(),
                end_time=timezone.now(),
                service_type='Direct',
                staff=self.admin,
                created_by=self.admin,
            )
            session = SessionRun.objects.create(
                external_client_id=int(self.client_row.external_id),
                staff=self.admin,
                program_snapshot={'programs': []},
                created_by=self.admin,
            )

            payload = link_session_appointment(
                self._request(),
                session.id,
                SessionLinkAppointmentRequest(
                    appointment_id=appointment.id,
                    external_appointment_id=appointment.external_id,
                ),
            )

            self.assertEqual(payload['appointment_id'], int(appointment.external_id))
