from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.clients.models import Client
from apps.programs.api import create_target, update_program
from apps.programs.models import Program, Target
from apps.programs.schemas import ProgramUpdateRequest, TargetCreateRequest
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class InstructionsOnlyProgramTests(TestCase):
    """Instructions Only programs (see Hi Rasmus's program type of the same
    name) exist purely to hold staff-facing reference info/materials — they
    must never be able to hold targets, matching the source feature's hard
    constraint."""

    def setUp(self):
        self.org = Organization.objects.create(
            name='Instructions Org', slug='instructions-org', schema_name='instructions_org',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.admin = User.objects.create_user(
            email='instructions-admin@example.com', password='x',
            first_name='Instructions', last_name='Admin', organization=self.org,
            role=User.Role.ADMIN, external_admin_id=701,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.client_row = Client.objects.create(
                first_name='Instructions', last_name='Client',
                external_id='9001', external_admin_id=701, organization=self.org,
            )
            self.program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Emergency Procedures',
                category=Program.Category.INSTRUCTIONS_ONLY,
                created_by=self.admin,
            )

    def _request(self):
        from types import SimpleNamespace
        return SimpleNamespace(user=self.admin)

    def test_cannot_add_a_target_to_an_instructions_only_program(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            data = TargetCreateRequest(name='Should not be allowed', measurement_type='discrete_trial')
            with self.assertRaises(HttpError):
                create_target(self._request(), self.program.id, data)
            self.assertEqual(self.program.targets.count(), 0)

    def test_cannot_switch_a_program_with_targets_to_instructions_only(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            other = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Manding', category=Program.Category.SKILL_ACQUISITION,
                created_by=self.admin,
            )
            Target.objects.create(program=other, name='Point to item', measurement_type='discrete_trial')

            data = ProgramUpdateRequest(category='instructions_only')
            with self.assertRaises(HttpError):
                update_program(self._request(), other.id, data)
            other.refresh_from_db()
            self.assertEqual(other.category, Program.Category.SKILL_ACQUISITION)

    def test_switching_to_instructions_only_is_fine_with_no_targets(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            empty = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='No Targets Yet', category=Program.Category.SKILL_ACQUISITION,
                created_by=self.admin,
            )
            data = ProgramUpdateRequest(category='instructions_only')
            update_program(self._request(), empty.id, data)
            empty.refresh_from_db()
            self.assertEqual(empty.category, Program.Category.INSTRUCTIONS_ONLY)
