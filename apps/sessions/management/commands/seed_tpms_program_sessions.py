"""
Seed DCM trial data onto real TherapyPMS provider sessions.

Usage:
    python manage.py seed_tpms_program_sessions --org dev --user-email admin@example.com --provider-id 123 --program-id 456
    python manage.py seed_tpms_program_sessions --org dev --user-id 7 --tpms-provider-id 98765 --program-id 456 --sessions 15 --replace-existing
    python manage.py seed_tpms_program_sessions --org dev --user-email admin@example.com --provider-id 123 --sessions 15 --replace-existing

The command expects the selected user to have a live TPMS access token in Redis.
That normally means the user has logged into DCM through TherapyPMS recently.
"""

from __future__ import annotations

import random
import zlib
from datetime import date, datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.clients.api import (
    _appointment_service_name,
    _dig_appointment,
    _parse_appointment_datetime,
    _tpms_status,
)
from apps.clients.models import Client
from apps.integrations.tpms_auth_client import (
    TpmsAuthError,
    get_tpms_access_token,
    list_provider_calendar,
)
from apps.programs.models import Lesson, LessonProgram, Program, PromptingTemplate, Target, TargetStatus
from apps.sessions.models import Appointment, SessionRun, TrialEvent
from apps.sessions.services import build_program_snapshot
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


TRIAL_MEASUREMENT_TYPES = {
    Target.MeasurementType.DISCRETE_TRIAL,
    Target.MeasurementType.TASK_ANALYSIS,
    Target.MeasurementType.SET_OF_TARGETS,
    Target.MeasurementType.SHAPING,
}

DEFAULT_TARGET_STATUSES = [
    {'key': 'waiting', 'label': 'Waiting', 'icon': 'hourglass', 'color': '#94a3b8', 'is_staff_visible': False, 'is_default': True, 'display_order': 0},
    {'key': 'probe', 'label': 'Probe', 'icon': 'clipboard-list', 'color': '#d97706', 'is_staff_visible': True, 'is_default': False, 'display_order': 1},
    {'key': 'acquisition', 'label': 'Acquisition', 'icon': 'graduation-cap', 'color': '#2563eb', 'is_staff_visible': True, 'is_default': False, 'display_order': 2},
    {'key': 'mastered', 'label': 'Mastered', 'icon': 'trophy', 'color': '#7c3aed', 'is_staff_visible': True, 'is_default': False, 'display_order': 3},
]

SEED_PROGRAMS = [
    {
        'name': 'Mand Training - Functional Communication',
        'treatment_area': 'Communication',
        'objective': 'Increase independent functional requests across natural opportunities.',
        'targets': ['Request preferred item', 'Request break', 'Request help', 'Request attention', 'Request more'],
    },
    {
        'name': 'Receptive Language - One-Step Instructions',
        'treatment_area': 'Language',
        'objective': 'Follow one-step receptive instructions across people and settings.',
        'targets': ['Come here', 'Sit down', 'Stand up', 'Give me item', 'Touch head', 'Clap hands'],
    },
    {
        'name': 'Expressive Language - Common Object Labels',
        'treatment_area': 'Language',
        'objective': 'Label common objects and pictures when presented.',
        'targets': ['Label ball', 'Label cup', 'Label shoe', 'Label book', 'Label dog', 'Label car'],
    },
    {
        'name': 'Visual Performance - Matching Skills',
        'treatment_area': 'Visual Performance',
        'objective': 'Match identical and related stimuli from an array.',
        'targets': ['Match identical pictures', 'Match identical objects', 'Match colors', 'Match shapes', 'Match letters'],
    },
    {
        'name': 'Motor Imitation - Gross Motor Actions',
        'treatment_area': 'Imitation',
        'objective': 'Imitate gross and fine motor actions following a model.',
        'targets': ['Imitate clap', 'Imitate raise arms', 'Imitate stomp feet', 'Imitate wave', 'Imitate tap table'],
    },
    {
        'name': 'Social Play - Turn Taking and Joint Attention',
        'treatment_area': 'Social Skills',
        'objective': 'Participate in reciprocal play and respond to social bids.',
        'targets': ['Take turns rolling ball', 'Respond to name', 'Share item', 'Wait for turn', 'Joint attention point'],
    },
    {
        'name': 'Listener Responding - Identification Skills',
        'treatment_area': 'Language',
        'objective': 'Identify body parts, actions, and common items receptively.',
        'targets': ['Identify nose', 'Identify eyes', 'Identify ears', 'Identify hands', 'Identify jumping', 'Identify eating', 'Identify cup'],
    },
    {
        'name': 'Daily Living Skills - Independence Routines',
        'treatment_area': 'Daily Living Skills',
        'objective': 'Complete daily living routines with reduced prompting.',
        'targets': ['Wash hands step', 'Put on shoes', 'Open lunch box', 'Clean up toys', 'Use napkin'],
    },
    {
        'name': 'Academic Readiness - Early Learner Skills',
        'treatment_area': 'School Readiness',
        'objective': 'Build early learner academic readiness skills.',
        'targets': ['Identify letter A', 'Identify number 1', 'Count objects to 3', 'Trace line', 'Sort by size', 'Point to name'],
    },
    {
        'name': 'Conversation Skills - Social Questions',
        'treatment_area': 'Communication',
        'objective': 'Improve answering and asking simple social questions.',
        'targets': ['Answer name question', 'Answer age question', 'Answer what doing', 'Ask for item', 'Ask where question'],
    },
    {
        'name': 'Safety Skills - Community and Home Safety',
        'treatment_area': 'Safety',
        'objective': 'Respond safely to common instructions and safety routines.',
        'targets': ['Stop on command', 'Wait at door', 'Walk with adult', 'Respond to no', 'Give dangerous item'],
    },
    {
        'name': 'Peer Interaction - Cooperative Play',
        'treatment_area': 'Social Skills',
        'objective': 'Increase appropriate peer interaction during structured activities.',
        'targets': ['Greet peer', 'Share material', 'Request turn', 'Respond to peer', 'Play beside peer'],
    },
]

LEGACY_SEED_PROGRAM_NAMES = [
    'TPMS Seed - Mand Training',
    'TPMS Seed - Receptive Instructions',
    'TPMS Seed - Expressive Labels',
    'TPMS Seed - Matching',
    'TPMS Seed - Motor Imitation',
    'TPMS Seed - Social Play',
    'TPMS Seed - Listener Responding',
    'TPMS Seed - Daily Living',
    'TPMS Seed - Academic Readiness',
    'TPMS Seed - Conversation Skills',
    'TPMS Seed - Safety Skills',
    'TPMS Seed - Peer Interaction',
]


class Command(BaseCommand):
    help = 'Seed programs, targets, and trial data on sessions fetched from TPMS for one provider'

    def add_arguments(self, parser):
        parser.add_argument('--org', required=True, help='Organization schema_name, slug, or id')
        parser.add_argument('--program-id', type=int, help='Existing DCM Program.id to attach. Omit to create/use a 10+ program seed pack.')
        parser.add_argument('--provider-id', type=int, help='Local DCM Client.id for the selected TPMS provider')
        parser.add_argument('--tpms-provider-id', type=int, help='Raw TPMS provider id, used when local Client.id is not known')
        parser.add_argument('--user-id', type=int, help='DCM User.id whose TPMS token should be used')
        parser.add_argument('--user-email', help='DCM user email whose TPMS token should be used')
        parser.add_argument('--sessions', type=int, default=15, help='How many TPMS sessions to seed')
        parser.add_argument('--program-count', type=int, default=10, help='How many seed programs to create/use when --program-id is omitted')
        parser.add_argument('--targets-min', type=int, default=3, help='Minimum targets per auto-seeded program')
        parser.add_argument('--targets-max', type=int, default=10, help='Maximum targets per auto-seeded program')
        parser.add_argument('--days-back', type=int, default=180, help='Calendar lookback window for TPMS sessions')
        parser.add_argument('--days-forward', type=int, default=30, help='Calendar lookahead window for TPMS sessions')
        parser.add_argument(
            '--preserve-tpms-dates',
            action='store_true',
            help='Keep TPMS appointment dates for seeded session/trial timestamps. By default, dates are shifted into the recent analytics window.',
        )
        parser.add_argument('--trials-per-target', type=int, default=10, help='Trials to create per target per session')
        parser.add_argument(
            '--replace-existing',
            action='store_true',
            help='Delete existing TrialEvent rows for this program targets in the selected sessions before seeding',
        )
        parser.add_argument(
            '--keep-existing-seed',
            action='store_true',
            help='Do not delete the previously auto-seeded program pack before recreating it',
        )
        parser.add_argument('--seed', type=int, default=42, help='Random seed for repeatable trial patterns')

    def handle(self, *args, **options):
        if not options.get('provider_id') and not options.get('tpms_provider_id'):
            raise CommandError('Pass either --provider-id or --tpms-provider-id')
        if not options.get('user_id') and not options.get('user_email'):
            raise CommandError('Pass either --user-id or --user-email')
        if options['sessions'] <= 0:
            raise CommandError('--sessions must be greater than 0')
        if options['trials_per_target'] <= 0:
            raise CommandError('--trials-per-target must be greater than 0')
        if options['program_count'] <= 0:
            raise CommandError('--program-count must be greater than 0')
        if options['targets_min'] <= 0 or options['targets_max'] < options['targets_min']:
            raise CommandError('--targets-min must be greater than 0 and --targets-max must be >= --targets-min')

        org = self._resolve_org(options['org'])
        with schema_context(org.schema_name), tenant_context(org.pk):
            random.seed(options['seed'])
            self._seed(org, options)

    def _resolve_org(self, ref: str) -> Organization:
        qs = Organization.objects.all()
        if str(ref).isdigit():
            org = qs.filter(id=int(ref)).first()
            if org:
                return org
        org = qs.filter(schema_name=ref).first() or qs.filter(slug=ref).first()
        if not org:
            raise CommandError(f'No Organization found for "{ref}"')
        return org

    def _resolve_user(self, *, user_id: int | None, user_email: str | None) -> User:
        if user_id:
            user = User.objects.filter(id=user_id).first()
        else:
            user = User.objects.filter(email__iexact=user_email).first()
        if not user:
            raise CommandError('User not found')
        return user

    def _resolve_provider(self, *, provider_id: int | None, tpms_provider_id: int | None) -> tuple[Client, int]:
        if provider_id:
            provider = Client.objects.filter(id=provider_id).first()
            if not provider:
                raise CommandError(f'Local provider/client {provider_id} not found')
            if provider.external_id and provider.external_id.isdigit():
                return provider, int(provider.external_id)
            if tpms_provider_id:
                provider.external_id = str(tpms_provider_id)
                provider.save(update_fields=['external_id', 'updated_at'])
                return provider, tpms_provider_id
            raise CommandError(
                f'Local provider/client {provider_id} has no numeric external_id. '
                'Pass --tpms-provider-id also.'
            )

        provider = Client.objects.filter(external_id=str(tpms_provider_id)).first()
        if not provider:
            raise CommandError(
                f'No local Client row found for TPMS provider {tpms_provider_id}. '
                'Sync/select the provider first, or pass --provider-id.'
            )
        return provider, int(tpms_provider_id)

    def _seed(self, org: Organization, options: dict[str, Any]) -> None:
        user = self._resolve_user(user_id=options.get('user_id'), user_email=options.get('user_email'))
        provider, tpms_provider_id = self._resolve_provider(
            provider_id=options.get('provider_id'),
            tpms_provider_id=options.get('tpms_provider_id'),
        )
        programs = self._resolve_programs(
            provider=provider,
            user=user,
            program_id=options.get('program_id'),
            program_count=options['program_count'],
            targets_min=options['targets_min'],
            targets_max=options['targets_max'],
            reset_existing_seed=not options['keep_existing_seed'],
        )
        program_targets = {
            program.id: list(
                program.targets
                .filter(is_visible_to_staff=True, measurement_type__in=TRIAL_MEASUREMENT_TYPES)
                .order_by('display_order', 'id')
            )
            for program in programs
        }
        empty_programs = [program.name for program in programs if not program_targets.get(program.id)]
        if empty_programs:
            raise CommandError(f'Some programs have no visible trial-style targets: {", ".join(empty_programs)}')

        token = get_tpms_access_token(user.id)
        if not token:
            raise CommandError(
                f'No TPMS token found for user {user.email}. Log in through TPMS first, then rerun this command.'
            )

        start_date = date.today() - timedelta(days=options['days_back'])
        end_date = date.today() + timedelta(days=options['days_forward'])
        try:
            appointments = list_provider_calendar(
                token,
                provider_ids=[tpms_provider_id],
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
        except TpmsAuthError as exc:
            raise CommandError(str(exc) or 'Failed to fetch TPMS provider calendar') from exc

        selected = self._select_appointments(appointments, tpms_provider_id, options['sessions'])
        if not selected:
            raise CommandError(f'No usable TPMS sessions found for provider {tpms_provider_id}')

        canonical_client_id = int(provider.external_id) if provider.external_id.isdigit() else provider.id
        total_trials = 0
        session_count = 0

        for idx, appt in enumerate(selected):
            with transaction.atomic():
                session = self._ensure_session(
                    appt=appt,
                    provider=provider,
                    canonical_client_id=canonical_client_id,
                    programs=programs,
                    user=user,
                    recent_index=None if options['preserve_tpms_dates'] else idx,
                    recent_total=None if options['preserve_tpms_dates'] else len(selected),
                )
                for program in programs:
                    created = self._seed_trials_for_session(
                        session=session,
                        program=program,
                        targets=program_targets[program.id],
                        session_index=idx,
                        session_total=len(selected),
                        trials_per_target=options['trials_per_target'],
                        replace_existing=options['replace_existing'],
                    )
                    total_trials += created
                session_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(programs)} program(s), '
            f'{sum(len(items) for items in program_targets.values())} target(s), '
            f'and {total_trials} trial event(s) '
            f'across {session_count} TPMS session(s) for provider "{provider.full_name}" '
            f'in organization "{org.schema_name}".'
        ))

    def _resolve_programs(
        self,
        *,
        provider: Client,
        user: User,
        program_id: int | None,
        program_count: int,
        targets_min: int,
        targets_max: int,
        reset_existing_seed: bool,
    ) -> list[Program]:
        if program_id:
            program = (
                Program.objects
                .select_related('prompting_template')
                .prefetch_related('targets__prompting_template', 'targets__child_items')
                .filter(id=program_id)
                .first()
            )
            if not program:
                raise CommandError(f'Program {program_id} not found')
            if program.external_client_id not in (None, provider.id):
                raise CommandError(
                    f'Program {program.id} belongs to client/provider {program.external_client_id}, '
                    f'not selected provider {provider.id}.'
            )
            return [program]

        if reset_existing_seed:
            self._clear_existing_seed_pack(provider)

        self._ensure_seed_defaults(user)
        prompt_tpl = PromptingTemplate.objects.get(name='Standard Prompt Hierarchy')
        count = min(program_count, len(SEED_PROGRAMS))
        selected_defs = SEED_PROGRAMS[:count]

        programs = []
        for order, row in enumerate(selected_defs):
            program, _ = Program.objects.update_or_create(
                external_client_id=provider.id,
                name=row['name'],
                defaults={
                    'is_template': False,
                    'prompting_template': prompt_tpl,
                    'category': Program.Category.SKILL_ACQUISITION,
                    'status': Program.Status.ACTIVE,
                    'phase': Program.Phase.ACTIVE,
                    'treatment_area': row['treatment_area'],
                    'objective': row['objective'],
                    'instructions': 'Collect trial-by-trial data during scheduled sessions and score independent responses using the prompt hierarchy.',
                    'instructions_html': '<p>Collect trial-by-trial data during scheduled sessions and score independent responses using the prompt hierarchy.</p>',
                    'display_order': order,
                    'created_by': user,
                },
            )
            target_names = row['targets'][:max(targets_min, min(targets_max, len(row['targets'])))]
            while len(target_names) < targets_min:
                target_names.append(f'{row["treatment_area"]} target {len(target_names) + 1}')

            program.targets.exclude(name__in=target_names).delete()
            for target_order, target_name in enumerate(target_names):
                Target.objects.update_or_create(
                    program=program,
                    name=target_name,
                    defaults={
                        'measurement_type': Target.MeasurementType.DISCRETE_TRIAL,
                        'measurement': Target.Measurement.PERCENT_CORRECT,
                        'status': 'acquisition' if target_order % 3 else 'probe',
                        'prompting_template': prompt_tpl,
                        'is_visible_to_staff': True,
                        'display_order': target_order,
                        'sd_text': target_name,
                        'teaching_instructions': 'Run 10 teaching opportunities and score independent responses.',
                        'instructions_html': '<p>Run 10 teaching opportunities and score independent responses.</p>',
                        'created_by': user,
                    },
                )
            programs.append(program)

        return list(
            Program.objects
            .select_related('prompting_template')
            .prefetch_related('targets__prompting_template', 'targets__child_items')
            .filter(id__in=[program.id for program in programs])
            .order_by('display_order', 'id')
        )

    def _clear_existing_seed_pack(self, provider: Client) -> None:
        seed_names = [row['name'] for row in SEED_PROGRAMS] + LEGACY_SEED_PROGRAM_NAMES
        programs = list(Program.objects.filter(external_client_id=provider.id, name__in=seed_names))
        if not programs:
            return

        program_ids = [program.id for program in programs]
        target_ids = list(Target.objects.filter(program_id__in=program_ids).values_list('id', flat=True))
        if target_ids:
            TrialEvent.objects.filter(target_id__in=target_ids).delete()
        LessonProgram.objects.filter(program_id__in=program_ids).delete()
        deleted, _ = Program.objects.filter(id__in=program_ids).delete()
        self.stdout.write(f'Cleared {deleted} existing auto-seeded program/target row(s) for provider {provider.full_name}.')

    def _ensure_seed_defaults(self, user: User) -> None:
        for row in DEFAULT_TARGET_STATUSES:
            TargetStatus.objects.get_or_create(key=row['key'], defaults=row)

        PromptingTemplate.objects.get_or_create(
            name='Standard Prompt Hierarchy',
            defaults={
                'description': 'Standard prompt levels for trial-by-trial data collection.',
                'levels': [
                    {'label': 'Full Physical', 'score': 0, 'color': '#ef4444', 'abbreviation': 'FP', 'is_success': False},
                    {'label': 'Partial Physical', 'score': 0, 'color': '#f97316', 'abbreviation': 'PP', 'is_success': False},
                    {'label': 'Model', 'score': 0, 'color': '#eab308', 'abbreviation': 'M', 'is_success': False},
                    {'label': 'Gestural', 'score': 0, 'color': '#3b82f6', 'abbreviation': 'G', 'is_success': False},
                    {'label': 'Independent', 'score': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                ],
                'outcome_measurement': PromptingTemplate.OutcomeMeasurement.BINARY,
                'is_org_default': False,
                'created_by': user,
            },
        )

    def _select_appointments(self, appointments: list[dict[str, Any]], provider_id: int, limit: int) -> list[dict[str, Any]]:
        usable = []
        for appt in appointments:
            row_provider_id = _dig_appointment(appt, 'provider_id', 'providerId', 'employee_id')
            try:
                if row_provider_id is not None and int(row_provider_id) != provider_id:
                    continue
            except (TypeError, ValueError):
                continue

            raw_status = str(_dig_appointment(appt, 'status', 'appointment_status') or '')
            if raw_status.lower() in {'deleted', 'void', 'voided', 'cancelled', 'canceled', 'no show', 'no-show'}:
                continue

            start = self._appointment_start(appt)
            ext_id = self._appointment_external_id(appt)
            if not start or not ext_id:
                continue
            usable.append((start, appt))

        usable.sort(key=lambda row: row[0])
        return [appt for _, appt in usable[-limit:]]

    def _ensure_session(
        self,
        *,
        appt: dict[str, Any],
        provider: Client,
        canonical_client_id: int,
        programs: list[Program],
        user: User,
        recent_index: int | None,
        recent_total: int | None,
    ) -> SessionRun:
        tpms_start = self._aware(self._appointment_start(appt)) or timezone.now()
        tpms_end = self._aware(self._appointment_end(appt, tpms_start)) or tpms_start
        if recent_index is not None and recent_total is not None:
            start = self._recent_session_start(tpms_start, recent_index, recent_total)
            duration = max(timedelta(minutes=30), tpms_end - tpms_start)
            end = start + duration
        else:
            start = tpms_start
            end = tpms_end
        ext_id = self._appointment_external_id(appt)
        appointment_key = self._appointment_key(ext_id)

        appointment, _ = Appointment.objects.get_or_create(
            external_id=ext_id,
            defaults={
                'external_client_id': provider.id,
                'source': Appointment.Source.SYNCED,
                'start_time': start,
                'end_time': end,
                'service_type': _appointment_service_name(appt)[:100],
                'status': _tpms_status(str(_dig_appointment(appt, 'status', 'appointment_status') or '')),
                'created_by': user,
            },
        )
        changed = False
        for field, value in (
            ('external_client_id', provider.id),
            ('start_time', start),
            ('end_time', end),
            ('service_type', _appointment_service_name(appt)[:100]),
        ):
            if getattr(appointment, field) != value:
                setattr(appointment, field, value)
                changed = True
        if changed:
            appointment.save(update_fields=['external_client_id', 'start_time', 'end_time', 'service_type', 'updated_at'])

        lesson = appointment.lesson
        if lesson is None:
            lesson = Lesson.objects.create(
                external_client_id=provider.id,
                name=start.strftime('Session %b %d, %Y'),
                lesson_type=Lesson.LessonType.APPOINTMENT_LINKED,
                created_by=user,
            )
            appointment.lesson = lesson
            appointment.save(update_fields=['lesson', 'updated_at'])

        for order, program in enumerate(programs):
            LessonProgram.objects.update_or_create(
                lesson=lesson,
                program=program,
                defaults={'display_order': order},
            )

        snapshot = build_program_snapshot(
            client_id=provider.id,
            lesson_id=lesson.id,
            restrict_to_lesson=True,
        )
        session, created = SessionRun.objects.get_or_create(
            external_client_id=canonical_client_id,
            external_appointment_id=appointment_key,
            defaults={
                'staff': user,
                'lesson': lesson,
                'status': SessionRun.Status.APPROVED,
                'started_at': start,
                'ended_at': end,
                'submitted_at': end,
                'reviewed_at': end,
                'reviewed_by': user,
                'program_snapshot': snapshot,
                'created_by': user,
            },
        )
        update_fields = []
        for field, value in (
            ('staff', user),
            ('lesson', lesson),
            ('status', SessionRun.Status.APPROVED),
            ('started_at', start),
            ('ended_at', end),
            ('submitted_at', end),
            ('reviewed_at', end),
            ('reviewed_by', user),
            ('program_snapshot', snapshot),
        ):
            if getattr(session, field) != value:
                setattr(session, field, value)
                update_fields.append(field)
        if update_fields:
            session.save(update_fields=[*update_fields, 'updated_at'])
        return session

    def _recent_session_start(self, original_start, index: int, total: int):
        anchor = timezone.now().replace(
            hour=original_start.hour,
            minute=original_start.minute,
            second=0,
            microsecond=0,
        )
        days_ago = max(0, total - index - 1)
        return anchor - timedelta(days=days_ago)

    def _seed_trials_for_session(
        self,
        *,
        session: SessionRun,
        program: Program,
        targets: list[Target],
        session_index: int,
        session_total: int,
        trials_per_target: int,
        replace_existing: bool,
    ) -> int:
        target_ids = [target.id for target in targets]
        if replace_existing:
            TrialEvent.objects.filter(session_run=session, target_id__in=target_ids).delete()

        progress = session_index / max(1, session_total - 1)
        trial_events: list[TrialEvent] = []
        session_start = session.started_at or timezone.now()

        existing_max = {
            row['target_id']: row['max_trial'] or 0
            for row in (
                TrialEvent.objects
                .filter(session_run=session, target_id__in=target_ids)
                .values('target_id')
                .annotate(max_trial=Max('trial_number'))
            )
        }

        for target in targets:
            success_score, success_label, miss_score, miss_label = self._scoring(program, target)
            base_accuracy = 0.45 + progress * 0.45
            target_accuracy = max(0.05, min(0.98, base_accuracy + random.uniform(-0.08, 0.08)))
            start_trial = existing_max.get(target.id, 0) + 1

            for offset in range(trials_per_target):
                trial_number = start_trial + offset
                is_success = random.random() < target_accuracy
                trial_events.append(TrialEvent(
                    organization_id=session.organization_id,
                    session_run=session,
                    target_id=target.id,
                    target_name=target.name,
                    trial_number=trial_number,
                    response_score=success_score if is_success else miss_score,
                    prompt_level_label=success_label if is_success else miss_label,
                    recorded_at=session_start + timedelta(minutes=offset + 1),
                    staff_notes='Seeded from TPMS provider session',
                ))

        TrialEvent.objects.bulk_create(trial_events, batch_size=500)
        return len(trial_events)

    def _scoring(self, program: Program, target: Target) -> tuple[int, str, int, str]:
        prompting = target.prompting_template or program.prompting_template
        levels = list((prompting.levels if prompting else []) or [])
        if not levels:
            return 1, 'Independent', 0, 'Prompted'

        success = next((level for level in levels if level.get('is_success')), None)
        if success is None:
            success = max(levels, key=lambda level: int(level.get('score') or 0))
        miss = next((level for level in levels if level is not success and int(level.get('score') or 0) < int(success.get('score') or 0)), None)
        if miss is None:
            miss = min(levels, key=lambda level: int(level.get('score') or 0))

        return (
            int(success.get('score') or 1),
            str(success.get('label') or 'Independent'),
            int(miss.get('score') or 0),
            str(miss.get('label') or 'Prompted'),
        )

    def _appointment_external_id(self, appt: dict[str, Any]) -> str:
        return str(
            _dig_appointment(
                appt,
                'session_id',
                'id',
                'appointment_id',
                'recurring_appointment_id',
                'recurring_session_id',
            ) or ''
        )

    def _appointment_key(self, ext_id: str) -> int:
        return int(ext_id) if ext_id.isdigit() else -zlib.crc32(ext_id.encode())

    def _appointment_start(self, appt: dict[str, Any]) -> datetime | None:
        return _parse_appointment_datetime(
            _dig_appointment(
                appt,
                'from_time',
                'start_time',
                'appointment_start_time',
                'schedule_from',
                'start',
            )
        )

    def _appointment_end(self, appt: dict[str, Any], start: datetime) -> datetime | None:
        parsed = _parse_appointment_datetime(
            _dig_appointment(appt, 'to_time', 'end_time', 'appointment_end_time', 'schedule_to', 'end')
        )
        if parsed:
            return parsed
        duration_raw = _dig_appointment(appt, 'time_duration', 'duration_minutes', 'duration')
        try:
            duration_minutes = int(duration_raw)
        except (TypeError, ValueError):
            duration_minutes = 0
        return start + timedelta(minutes=duration_minutes or 60)

    def _aware(self, dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
