"""
Central Library import: CentralProgram/CentralTarget (apps.central_library)
are plain shared-schema rows, not tied to any Organization. Importing one
must land in the *calling* org — including a fresh, org-owned
PromptingTemplate built from the target's optional `prompting_levels`,
since PromptingTemplate is tenant-scoped and there is no org to reference.
"""
from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.central_library.models import CentralProgram, CentralTarget
from apps.programs.api import _clone_central_program
from apps.programs.models import PromptingTemplate
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class CentralLibraryImportTests(TestCase):
    def setUp(self):
        self.dest_org = Organization.objects.create(
            name='Test Org B', slug='test-org-b', schema_name='test_org_b',
        )
        self.user = User.objects.create_user(
            email='importer@example.com', password='x',
            first_name='Importer', last_name='User', organization=self.dest_org,
        )

        self.source_program = CentralProgram.objects.create(
            name='Central Program', category=CentralProgram.Category.SKILL_ACQUISITION,
        )
        CentralTarget.objects.create(
            program=self.source_program,
            name='Central Target',
            prompting_levels=[{'label': 'Independent', 'score': 1}],
        )

    def test_import_creates_program_and_target_in_calling_org(self):
        with schema_context(self.dest_org.schema_name), tenant_context(self.dest_org.pk):
            dest = _clone_central_program(self.source_program.id, self.user)

            self.assertEqual(dest.organization_id, self.dest_org.pk)
            self.assertEqual(dest.name, self.source_program.name)
            self.assertTrue(dest.is_template)

            dest_target = dest.targets.get()
            self.assertEqual(dest_target.organization_id, self.dest_org.pk)
            self.assertEqual(dest_target.name, 'Central Target')

            # prompting_levels became a new, org-owned PromptingTemplate —
            # not a shared reference back to any central row.
            self.assertIsNotNone(dest_target.prompting_template_id)
            self.assertEqual(dest_target.prompting_template.organization_id, self.dest_org.pk)
            self.assertEqual(
                dest_target.prompting_template.levels,
                [{'label': 'Independent', 'score': 1}],
            )
            self.assertEqual(PromptingTemplate.objects.count(), 1)

    def test_import_without_prompting_levels_leaves_target_unset(self):
        CentralTarget.objects.create(program=self.source_program, name='No Prompting Target')

        with schema_context(self.dest_org.schema_name), tenant_context(self.dest_org.pk):
            dest = _clone_central_program(self.source_program.id, self.user)
            bare_target = dest.targets.get(name='No Prompting Target')
            self.assertIsNone(bare_target.prompting_template_id)
