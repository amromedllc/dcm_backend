"""Note templates can insert program values from the session the note is for."""
from datetime import date, datetime, timedelta, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.notes.models import LessonNote, NoteTemplate
from apps.notes.services import resolve_program_tokens, resolve_template_tokens
from apps.programs.models import Program, PromptingTemplate, Target, TargetPromptLevelChange, TargetStatusChange
from apps.sessions.models import BehaviorEvent, SessionRun, TrialEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class ProgramTokenTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Token Org', slug='token-org', schema_name='token_org')
        self.staff = User.objects.create_user(
            email='token-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            rating = PromptingTemplate.objects.create(
                name='0-2', outcome_measurement='rating_scale',
                levels=[{'label': 'No', 'score': 0}, {'label': 'Yes', 'score': 2, 'is_success': True}],
            )
            self.manding = Program.objects.create(name='Manding', category='skill_acquisition', external_client_id=1)
            self.calm = Program.objects.create(name='Calm Time', category='skill_acquisition', external_client_id=1)
            self.asks = Target.objects.create(
                program=self.manding, name='Asks for snack', measurement_type='discrete_trial', prompting_template=rating,
            )
            self.seated = Target.objects.create(program=self.calm, name='Stays seated', measurement_type='duration')
            self.waits = Target.objects.create(program=self.calm, name='Waits', measurement_type='frequency')
            self.unused = Target.objects.create(program=self.manding, name='Unused target', measurement_type='discrete_trial')
            when = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
            self.session = SessionRun.objects.create(external_client_id=1, staff=self.staff, started_at=when)
            for number, score in enumerate([2, 2, 0, 2], start=1):   # 3 of 4 successful on a 0-2 scale
                TrialEvent.objects.create(
                    session_run=self.session, target_id=self.asks.id, target_name=self.asks.name, trial_number=number,
                    response_score=score, prompt_level_label='', recorded_at=when,
                )
            for seconds in (300, 45):
                BehaviorEvent.objects.create(
                    session_run=self.session, target_id=self.seated.id, target_name=self.seated.name,
                    occurred_at=when, duration_seconds=seconds, frequency_count=1,
                )
            for _ in range(3):
                BehaviorEvent.objects.create(
                    session_run=self.session, target_id=self.waits.id, target_name=self.waits.name,
                    occurred_at=when, frequency_count=1,
                )
            TargetStatusChange.objects.create(
                target=self.asks, from_status='teaching', to_status='maintenance',
                trigger='auto_mastery', session_run_id=self.session.id,
            )
            TargetPromptLevelChange.objects.create(
                target=self.asks, from_level_index=0, to_level_index=1, from_level_label='Model',
                to_level_label='Gesture', trigger='auto_fading', session_run_id=self.session.id,
            )
            # a manual change and another session's change must not appear
            TargetStatusChange.objects.create(
                target=self.seated, from_status='a', to_status='b', trigger='manual', session_run_id=self.session.id,
            )

    def _ctx(self):
        return schema_context(self.org.schema_name), tenant_context(self.org.pk)

    def test_lists_programs_and_target_results_from_the_session(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            values = resolve_program_tokens(self.session)
        self.assertEqual(values['program.names'], 'Calm Time, Manding')
        lines = values['program.targets_results'].split('\n')
        self.assertIn('Manding — Asks for snack: 75% correct (3 of 4 trials)', lines)
        self.assertIn('Calm Time — Stays seated: 2 timed occurrences, total 0:05:45', lines)
        self.assertIn('Calm Time — Waits: 3 occurrences', lines)
        self.assertFalse(any('Unused target' in line for line in lines))    # nothing recorded for it

    def test_advancements_and_prompt_changes_come_only_from_this_sessions_automatic_changes(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            values = resolve_program_tokens(self.session)
        self.assertEqual(values['program.targets_advanced'], 'Asks for snack: Teaching → Maintenance')
        self.assertEqual(values['program.prompt_changes'], 'Asks for snack: Model → Gesture')

    def test_a_session_with_no_data_only_names_its_programs_from_the_snapshot(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            empty = SessionRun.objects.create(
                external_client_id=1, staff=self.staff, program_snapshot={'programs': [{'name': 'Manding'}]},
            )
            values = resolve_program_tokens(empty)
        self.assertEqual(values, {'program.names': 'Manding'})

    def test_tokens_reach_the_note_via_the_template_resolver(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            template = NoteTemplate.objects.create(
                name='Progress note', template_type='forms', body_template='<p>[Targets and Results]</p>',
            )
            note = LessonNote.objects.create(
                session_run=self.session, external_client_id=1, staff=self.staff,
                template=template, note_date=date(2026, 5, 1),
            )
            values = resolve_template_tokens(note)
        self.assertIn('program.targets_results', values)
        self.assertEqual(values['session.date'], '2026-05-01')


class SessionNoteAutofillTests(ProgramTokenTests):
    """Session notes (template type 'notes') can fill fields from the session's program data."""

    def _note(self, body=None, template_fields=None):
        template = NoteTemplate.objects.create(
            name='Session note', template_type='notes',
            fields=template_fields or [
                {'key': 'summary', 'label': 'Summary', 'type': 'textarea', 'required': False, 'auto_fill': ''},
                {'key': 'targets_worked', 'label': 'Targets worked on', 'type': 'textarea', 'required': False,
                 'auto_fill': 'program.targets_results'},
                {'key': 'programs', 'label': 'Programs', 'type': 'text', 'required': False, 'auto_fill': 'program.names'},
            ],
        )
        return LessonNote.objects.create(
            session_run=self.session, external_client_id=1, staff=self.staff,
            template=template, note_date=date(2026, 5, 1), body=body or {},
        )

    def test_fills_only_the_marked_fields_and_keeps_what_the_author_typed(self):
        from apps.notes.services import apply_session_autofill
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            note = self._note(body={'summary': 'Great session', 'programs': 'typed by hand'})
            changed = apply_session_autofill(note, overwrite=False)
            note.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(note.body['summary'], 'Great session')            # not auto-fill: untouched
        self.assertEqual(note.body['programs'], 'typed by hand')           # already typed: kept
        self.assertIn('Manding — Asks for snack: 75% correct (3 of 4 trials)', note.body['targets_worked'])

    def test_refill_replaces_the_auto_fill_fields_with_fresh_data(self):
        from apps.notes.services import apply_session_autofill
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            note = self._note(body={'programs': 'stale', 'targets_worked': 'stale', 'summary': 'keep me'})
            apply_session_autofill(note, overwrite=True)
            note.refresh_from_db()
        self.assertEqual(note.body['programs'], 'Calm Time, Manding')
        self.assertIn('Stays seated', note.body['targets_worked'])
        self.assertEqual(note.body['summary'], 'keep me')

    def test_notes_without_a_session_or_auto_fill_fields_are_left_alone(self):
        from apps.notes.services import apply_session_autofill
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            no_fill = self._note(template_fields=[{'key': 'a', 'label': 'A', 'type': 'text', 'required': False}])
            self.assertFalse(apply_session_autofill(no_fill, overwrite=False))
            template = NoteTemplate.objects.create(
                name='No session', template_type='notes',
                fields=[{'key': 'x', 'label': 'X', 'type': 'text', 'required': False, 'auto_fill': 'program.names'}],
            )
            orphan = LessonNote.objects.create(
                external_client_id=1, staff=self.staff, template=template, note_date=date(2026, 5, 1),
            )
            self.assertFalse(apply_session_autofill(orphan, overwrite=False))
