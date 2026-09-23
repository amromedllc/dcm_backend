"""Program prototypes: reusable presets that pre-fill the Create Program form."""
from types import SimpleNamespace

from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.programs.api import (
    create_program_prototype, delete_program_prototype,
    list_program_prototypes, update_program_prototype,
)
from apps.programs.models import ProgramPrototype, PromptingTemplate
from apps.programs.schemas import ProgramPrototypeRequest, ProgramPrototypeUpdateRequest
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class ProgramPrototypeApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Proto Org', slug='proto-org', schema_name='proto_org',
        )
        self.admin = User.objects.create_user(
            email='proto-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email='proto-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def test_admin_can_create_list_update_delete(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            request = self._request(self.admin)
            _status, proto = create_program_prototype(
                request,
                ProgramPrototypeRequest(
                    name='Manding', category='skill_acquisition',
                    treatment_area='Communication', tags=['mands'],
                    objective='Learner requests items.',
                ),
            )
            self.assertEqual(proto.name, 'Manding')
            self.assertEqual([p.name for p in list_program_prototypes(request)], ['Manding'])

            updated = update_program_prototype(
                request, proto.id, ProgramPrototypeUpdateRequest(is_active=False, objective='New'),
            )
            self.assertEqual(updated.objective, 'New')
            self.assertEqual(list(list_program_prototypes(request)), [])
            self.assertEqual(len(list_program_prototypes(request, include_inactive=True)), 1)

            delete_program_prototype(request, proto.id)
            self.assertEqual(ProgramPrototype.objects.count(), 0)

    def test_staff_cannot_create(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with self.assertRaises(HttpError) as ctx:
                create_program_prototype(self._request(self.staff), ProgramPrototypeRequest(name='Nope'))
            self.assertEqual(ctx.exception.status_code, 403)

    def test_duplicate_name_is_rejected(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            request = self._request(self.admin)
            create_program_prototype(request, ProgramPrototypeRequest(name='Same'))
            with self.assertRaises(HttpError) as ctx:
                create_program_prototype(request, ProgramPrototypeRequest(name='Same'))
            self.assertEqual(ctx.exception.status_code, 409)

    def test_unknown_template_is_rejected_and_template_can_be_cleared(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            request = self._request(self.admin)
            with self.assertRaises(HttpError) as ctx:
                create_program_prototype(
                    request, ProgramPrototypeRequest(name='Bad', prompting_template_id=99999),
                )
            self.assertEqual(ctx.exception.status_code, 400)

            template = PromptingTemplate.objects.create(
                name='Std', levels=[{'label': 'Independent', 'is_success': True}], created_by=self.admin,
            )
            _s, proto = create_program_prototype(
                request, ProgramPrototypeRequest(name='Good', prompting_template_id=template.id),
            )
            self.assertEqual(proto.prompting_template_id, template.id)
            cleared = update_program_prototype(
                request, proto.id, ProgramPrototypeUpdateRequest(prompting_template_id=None),
            )
            self.assertIsNone(cleared.prompting_template_id)
