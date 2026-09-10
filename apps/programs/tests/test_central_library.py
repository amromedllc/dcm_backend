"""
Central Library import: CentralProgram/CentralTarget (apps.central_library)
are plain shared-schema rows, not tied to any Organization. Importing one
must land in the *calling* org — including a fresh, org-owned
PromptingTemplate built from the target's optional `prompting_levels`,
since PromptingTemplate is tenant-scoped and there is no org to reference.
"""
from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.central_library.models import (
    CentralProgram, CentralProgramFolder, CentralTarget,
    KnowledgeBaseModule, KnowledgeBaseTopic,
)
from apps.programs.api import (
    _clone_central_program,
    superadmin_create_central_program,
    superadmin_create_central_target,
    superadmin_create_knowledge_base_module,
    superadmin_create_knowledge_base_topic,
    superadmin_list_central_programs,
    superadmin_list_knowledge_base_modules,
    superadmin_update_central_program,
    superadmin_update_knowledge_base_module,
)
from apps.programs.models import PromptingTemplate
from apps.programs.schemas import (
    CentralProgramRequest, CentralProgramUpdateRequest, CentralTargetRequest,
    KnowledgeBaseModuleRequest, KnowledgeBaseModuleUpdateRequest, KnowledgeBaseTopicRequest,
)
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class FakeRequest:
    def __init__(self, user):
        self.user = user

    def build_absolute_uri(self, value):
        return f'http://testserver{value}'


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


class SuperadminCentralProgramApiTests(TestCase):
    def setUp(self):
        self.folder = CentralProgramFolder.objects.create(name='Communication')
        self.superadmin = User.objects.create_user(
            email='super-central@example.com',
            password='x',
            first_name='Super',
            last_name='Central',
            role=User.Role.ADMIN,
            is_superuser=True,
        )
        self.admin = User.objects.create_user(
            email='org-admin-central@example.com',
            password='x',
            first_name='Org',
            last_name='Admin',
            role=User.Role.ADMIN,
        )

    def test_superadmin_can_create_and_list_central_program(self):
        _status, program = superadmin_create_central_program(
            FakeRequest(self.superadmin),
            CentralProgramRequest(
                name='Requesting Help',
                category='skill_acquisition',
                phase='teaching',
                status='active',
                treatment_area='Communication',
                tags=['mands'],
                objective='Learner requests help.',
                instructions='Teach a functional help response.',
                folder_id=self.folder.id,
                display_order=2,
            ),
        )

        rows = superadmin_list_central_programs(FakeRequest(self.superadmin))

        self.assertEqual(program['name'], 'Requesting Help')
        self.assertEqual(rows[0]['folder_id'], self.folder.id)
        self.assertEqual(rows[0]['target_count'], 0)

    def test_regular_admin_cannot_create_central_program(self):
        with self.assertRaises(HttpError) as ctx:
            superadmin_create_central_program(
                FakeRequest(self.admin),
                CentralProgramRequest(name='Blocked'),
            )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_superadmin_can_update_program_and_create_target(self):
        program = CentralProgram.objects.create(name='Old Name', folder=self.folder)

        updated = superadmin_update_central_program(
            FakeRequest(self.superadmin),
            program.id,
            CentralProgramUpdateRequest(name='New Name', folder_id=None),
        )
        _status, target = superadmin_create_central_target(
            FakeRequest(self.superadmin),
            program.id,
            CentralTargetRequest(
                name='Ask for help',
                measurement_type='rate',
                timer_type='count_up',
                display_order=1,
                prompting_levels=[{'label': 'Independent', 'score': 1}],
            ),
        )

        program.refresh_from_db()
        self.assertEqual(updated['name'], 'New Name')
        self.assertIsNone(program.folder_id)
        self.assertEqual(target['measurement'], 'rate_per_minute')
        self.assertEqual(CentralTarget.objects.get().prompting_levels[0]['label'], 'Independent')


class SuperadminKnowledgeBaseApiTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email='super-kb@example.com',
            password='x',
            first_name='Super',
            last_name='Knowledge',
            role=User.Role.ADMIN,
            is_superuser=True,
        )
        self.admin = User.objects.create_user(
            email='org-admin-kb@example.com',
            password='x',
            first_name='Org',
            last_name='Admin',
            role=User.Role.ADMIN,
        )

    def test_superadmin_can_create_article_and_topic(self):
        _status, article = superadmin_create_knowledge_base_module(
            FakeRequest(self.superadmin),
            KnowledgeBaseModuleRequest(
                slug='dashboard',
                title='Dashboard',
                path='/dashboard',
                icon='bar_chart',
                overview='Dashboard article overview.',
                audience=['Admin', 'Supervisor'],
                display_order=1,
                is_active=True,
            ),
        )
        _topic_status, topic = superadmin_create_knowledge_base_topic(
            FakeRequest(self.superadmin),
            article['id'],
            KnowledgeBaseTopicRequest(
                title='Review Queue',
                summary='How to use the queue.',
                items=['Open dashboard', 'Review pending items'],
                display_order=2,
            ),
        )

        rows = superadmin_list_knowledge_base_modules(FakeRequest(self.superadmin))

        self.assertEqual(rows[0]['title'], 'Dashboard')
        self.assertEqual(rows[0]['topics'][0]['title'], 'Review Queue')
        self.assertEqual(topic['items'], ['Open dashboard', 'Review pending items'])

    def test_regular_admin_cannot_create_article(self):
        with self.assertRaises(HttpError) as ctx:
            superadmin_create_knowledge_base_module(
                FakeRequest(self.admin),
                KnowledgeBaseModuleRequest(
                    slug='blocked',
                    title='Blocked',
                    overview='Blocked overview.',
                ),
            )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_superadmin_can_hide_article(self):
        article = KnowledgeBaseModule.objects.create(
            slug='reports',
            title='Reports',
            overview='Reports overview.',
            is_active=True,
        )
        KnowledgeBaseTopic.objects.create(module=article, title='Exports')

        result = superadmin_update_knowledge_base_module(
            FakeRequest(self.superadmin),
            article.id,
            KnowledgeBaseModuleUpdateRequest(is_active=False),
        )

        article.refresh_from_db()
        self.assertIs(result['is_active'], False)
        self.assertIs(article.is_active, False)
