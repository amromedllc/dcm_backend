"""Assessment programs: the starter template and the per-area summary."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.analytics.services import get_assessment_summary
from apps.programs.api import (
    ASSESSMENT_STARTER_AREAS, ASSESSMENT_STARTER_NAME, create_assessment_starter,
)
from apps.programs.models import Program, ProgramModule, PromptingTemplate, Target
from apps.sessions.models import SessionRun, TrialEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class AssessmentStarterTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Starter Org', slug='starter-org', schema_name='starter_org')
        self.admin = User.objects.create_user(
            email='starter-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email='starter-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def test_creates_a_blank_rating_scale_assessment_template(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            code, program = create_assessment_starter(self._request(self.admin))
            row = Program.objects.get(pk=program['id'])
            modules = list(ProgramModule.objects.filter(program=row).values_list('name', flat=True))
            targets = list(Target.objects.filter(program=row))
            template = PromptingTemplate.objects.get(pk=row.prompting_template_id)
        self.assertEqual(code, 201)
        self.assertEqual(row.category, 'assessment')
        self.assertTrue(row.is_template)
        self.assertEqual(row.name, ASSESSMENT_STARTER_NAME)
        self.assertEqual(modules, ASSESSMENT_STARTER_AREAS)
        self.assertEqual(len(targets), len(ASSESSMENT_STARTER_AREAS) * 3)
        self.assertTrue(all(t.module_id and 'replace with your own' in t.name for t in targets))
        self.assertEqual(template.outcome_measurement, 'rating_scale')
        self.assertEqual([lvl['score'] for lvl in template.levels], [0, 1, 2])

    def test_second_call_returns_the_existing_starter(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            _c, first = create_assessment_starter(self._request(self.admin))
            code, second = create_assessment_starter(self._request(self.admin))
            count = Program.objects.filter(name=ASSESSMENT_STARTER_NAME).count()
        self.assertEqual(code, 200)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(count, 1)

    def test_needs_permission_to_create_programs(self):
        from ninja.errors import HttpError
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with self.assertRaises(HttpError) as raised:
                create_assessment_starter(self._request(self.staff))
        self.assertEqual(raised.exception.status_code, 403)


class AssessmentSummaryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Summary Org', slug='summary-org', schema_name='summary_org')
        self.staff = User.objects.create_user(
            email='summary-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            rating = PromptingTemplate.objects.create(
                name='0-2', levels=[
                    {'label': 'No', 'score': 0, 'is_success': False},
                    {'label': 'Some', 'score': 1, 'is_success': False},
                    {'label': 'Yes', 'score': 2, 'is_success': True},
                ], outcome_measurement='rating_scale',
            )
            self.program = Program.objects.create(
                name='Check', category='assessment', external_client_id=1, prompting_template=rating,
            )
            language = ProgramModule.objects.create(program=self.program, name='Language', display_order=0)
            social = ProgramModule.objects.create(program=self.program, name='Social', display_order=10)
            self.request_skill = Target.objects.create(
                program=self.program, module=language, name='Requests', measurement_type='discrete_trial', prompting_template=rating,
            )
            self.name_skill = Target.objects.create(
                program=self.program, module=language, name='Names items', measurement_type='discrete_trial', prompting_template=rating,
            )
            self.turns_skill = Target.objects.create(
                program=self.program, module=social, name='Takes turns', measurement_type='discrete_trial', prompting_template=rating,
            )
            first = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
            second = first + timedelta(days=30)
            self.first_session = SessionRun.objects.create(external_client_id=1, staff=self.staff, started_at=first)
            self.second_session = SessionRun.objects.create(external_client_id=1, staff=self.staff, started_at=second)
            # Requests: first 0 then 2 (0% -> 100%); Names items: first 1 only (50%); Takes turns: never scored
            self._score(self.first_session, self.request_skill, 0, first)
            self._score(self.second_session, self.request_skill, 2, second)
            self._score(self.first_session, self.name_skill, 1, first)

    def _score(self, session, target, score, when):
        TrialEvent.objects.create(
            session_run=session, target_id=target.id, target_name=target.name, trial_number=1,
            response_score=score, prompt_level_label='', recorded_at=when,
        )

    def test_scores_are_a_percentage_of_the_scale_maximum_by_area(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            summary = get_assessment_summary(self.program.id)
        areas = {a['module_name']: a for a in summary['areas']}
        language = areas['Language']
        skills = {s['target_name']: s for s in language['skills']}
        self.assertEqual(skills['Requests']['latest_pct'], 100.0)
        self.assertEqual(skills['Requests']['previous_pct'], 0.0)
        self.assertEqual(skills['Requests']['change'], 100.0)
        self.assertEqual(skills['Names items']['latest_pct'], 50.0)
        self.assertIsNone(skills['Names items']['change'])
        self.assertEqual(language['total_skills'], 2)
        self.assertEqual(language['scored_skills'], 2)
        self.assertEqual(language['latest_avg_pct'], 75.0)
        self.assertEqual(language['change'], 100.0)         # only Requests has a previous score
        self.assertEqual(str(language['last_date']), '2026-05-01')

    def test_unscored_areas_are_listed_with_no_score(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            summary = get_assessment_summary(self.program.id)
        social = next(a for a in summary['areas'] if a['module_name'] == 'Social')
        self.assertEqual((social['total_skills'], social['scored_skills']), (1, 0))
        self.assertIsNone(social['latest_avg_pct'])
        self.assertIsNone(social['last_date'])
        self.assertEqual([a['module_name'] for a in summary['areas']], ['Language', 'Social'])
