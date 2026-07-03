"""
Seed realistic programs, targets, workflow templates, and session history for a client.

Usage:
    python manage.py seed_client_programs --schema dev --client-id 7616
    python manage.py seed_client_programs --schema dev --client-id 7616 --clear
    python manage.py seed_client_programs --schema dev --client-id 7616 --clear --seed-sessions --days 30
"""

import random
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from apps.programs.models import Program, Target, WorkflowTemplate, PromptingTemplate


WORKFLOWS = [
    {
        'name': 'Standard DTT Workflow',
        'description': 'Probe → Acquisition → Mastered progression for discrete trial training.',
        'phases': [
            {
                'phase': 'probe',
                'criteria': {'consecutive_sessions': 1, 'threshold_pct': 100, 'minimum_trials': 3},
                'on_success': 'acquisition',
                'on_regression': None,
            },
            {
                'phase': 'acquisition',
                'criteria': {'consecutive_sessions': 3, 'threshold_pct': 80, 'minimum_trials': 5},
                'on_success': 'mastered',
                'on_regression': 'probe',
            },
            {
                'phase': 'mastered',
                'criteria': {'consecutive_sessions': 2, 'threshold_pct': 90, 'minimum_trials': 5},
                'on_success': 'maintenance',
                'on_regression': 'acquisition',
            },
        ],
    },
    {
        'name': 'Behavior Reduction Workflow',
        'description': 'Tracks frequency/duration toward reduction goals.',
        'phases': [
            {
                'phase': 'baseline',
                'criteria': {'consecutive_sessions': 3, 'threshold_pct': 0, 'minimum_trials': 1},
                'on_success': 'acquisition',
                'on_regression': None,
            },
            {
                'phase': 'acquisition',
                'criteria': {'consecutive_sessions': 5, 'threshold_pct': 20, 'minimum_trials': 1},
                'on_success': 'mastered',
                'on_regression': 'baseline',
            },
        ],
    },
]

PROGRAMS = [
    # ── Skill Acquisition ──────────────────────────────────────────────────────
    {
        'name': 'Mand Training — Basic',
        'category': 'skill_acquisition',
        'treatment_area': 'Communication',
        'phase': 'teaching',
        'objective': 'Client will independently request preferred items, activities, and breaks using vocal speech or AAC device across 3 consecutive sessions with 80% accuracy.',
        'instructions': 'Use the PECS or vocal mand protocol. Present the preferred item just out of reach. Wait 3–5 seconds for a spontaneous mand before prompting. Reinforce immediately.',
        'tags': ['Communication', 'Verbal Behavior'],
        'targets': [
            {'name': 'Request preferred snack', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Present snack just out of reach, pause 5s'},
            {'name': 'Request break', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Present task demand, wait for mand'},
            {'name': 'Request preferred toy', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': 'Hold toy visible but out of reach'},
            {'name': 'Request help', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Present difficult task, wait for "help" mand'},
        ],
    },
    {
        'name': 'Receptive Language — Body Parts',
        'category': 'skill_acquisition',
        'treatment_area': 'Language',
        'phase': 'teaching',
        'objective': 'Client will identify 10 body parts by pointing when asked "Show me ___" with 90% accuracy across 3 consecutive sessions.',
        'instructions': 'Use a card or doll for receptive identification. Mix targets across trials. Use errorless learning initially, fading prompts systematically.',
        'tags': ['Language', 'Receptive'],
        'targets': [
            {'name': 'Identify nose', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': 'Show me your nose'},
            {'name': 'Identify ears', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': 'Show me your ears'},
            {'name': 'Identify eyes', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Show me your eyes'},
            {'name': 'Identify hands', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Show me your hands'},
            {'name': 'Identify feet', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': 'Show me your feet'},
            {'name': 'Identify belly', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Show me your belly'},
        ],
    },
    {
        'name': 'Self-Care — Hand Washing',
        'category': 'skill_acquisition',
        'treatment_area': 'Daily Living Skills',
        'phase': 'teaching',
        'objective': 'Client will independently complete all steps of hand-washing routine with 90% accuracy across 3 sessions.',
        'instructions': 'Use a visual task analysis posted at the sink. Provide gestural prompts only; avoid full physical. Reinforce at the end of the chain.',
        'tags': ['ADL', 'Independence'],
        'targets': [
            {'name': 'Turn on water', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': 'Go wash your hands'},
            {'name': 'Wet hands', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': 'Continue washing hands'},
            {'name': 'Apply soap', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Continue washing hands'},
            {'name': 'Scrub for 20 seconds', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Continue washing hands'},
            {'name': 'Rinse hands', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': 'Continue washing hands'},
            {'name': 'Dry with towel', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Continue washing hands'},
        ],
    },
    # ── Behavior Reduction ────────────────────────────────────────────────────
    {
        'name': 'Self-Injurious Behavior — Head Hitting',
        'category': 'behavior_reduction',
        'treatment_area': 'Behavior Management',
        'phase': 'teaching',
        'objective': 'Reduce frequency of head-hitting behavior to 0–2 occurrences per session across 5 consecutive sessions.',
        'instructions': 'Record each occurrence using frequency count. Implement DRO schedule. Antecedent: avoid known triggers. Consequence: withhold attention, redirect to functional activity.',
        'tags': ['SIB', 'Behavior Reduction'],
        'targets': [
            {'name': 'Head hitting', 'measurement_type': 'frequency', 'status': 'acquisition', 'sd_text': 'Record each occurrence of head hitting (palm or object)'},
        ],
    },
    {
        'name': 'Tantrum Behavior',
        'category': 'behavior_reduction',
        'treatment_area': 'Behavior Management',
        'phase': 'teaching',
        'objective': 'Reduce duration of tantrum episodes to under 2 minutes per session average.',
        'instructions': 'Record start and end time of each tantrum. Tantrum defined as: crying, screaming, or dropping to floor lasting >10 seconds. Use planned ignoring unless safety is a concern.',
        'tags': ['Behavior Reduction', 'Emotion Regulation'],
        'targets': [
            {'name': 'Tantrum duration', 'measurement_type': 'duration', 'status': 'acquisition', 'sd_text': 'Record total duration of tantrum episode in seconds'},
        ],
    },
    # ── ABC Recording ─────────────────────────────────────────────────────────
    {
        'name': 'ABC Data — Aggression',
        'category': 'abc_recording',
        'treatment_area': 'Behavior Analysis',
        'phase': 'baseline',
        'objective': 'Collect ABC data across 10 sessions to identify function and antecedents of aggressive behavior.',
        'instructions': 'Complete an ABC form for every aggressive episode. Antecedent: describe what happened immediately before. Behavior: describe the topography. Consequence: what happened after.',
        'tags': ['ABC', 'Functional Analysis'],
        'targets': [
            {'name': 'Aggression toward peers', 'measurement_type': 'frequency', 'status': 'acquisition', 'sd_text': 'Record each aggressive act toward another person'},
            {'name': 'Property destruction', 'measurement_type': 'frequency', 'status': 'acquisition', 'sd_text': 'Record each instance of throwing or breaking objects'},
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed programs, targets, and workflow templates for a client'

    def add_arguments(self, parser):
        parser.add_argument('--schema', required=True, help='Tenant schema name (e.g. dev)')
        parser.add_argument('--client-id', type=int, required=True, help='Client ID to seed programs for')
        parser.add_argument('--clear', action='store_true', help='Delete existing client programs and sessions first')
        parser.add_argument('--seed-sessions', action='store_true', help='Also seed session + trial history')
        parser.add_argument('--days', type=int, default=30, help='Days of session history (default 30)')

    def handle(self, *args, **options):
        with schema_context(options['schema']):
            self._seed(options['client_id'], options['clear'], options['seed_sessions'], options['days'])

    def _seed(self, client_id: int, clear: bool, seed_sessions: bool, days: int):
        from apps.clients.models import Client
        from apps.accounts.models import User
        try:
            client = Client.objects.get(id=client_id)
        except Client.DoesNotExist:
            raise CommandError(f'Client {client_id} not found')

        staff = User.objects.filter(role__in=['admin', 'supervisor', 'therapist']).first()
        if not staff:
            raise CommandError('No staff user found — create one first.')

        self.stdout.write(f'Seeding data for client: {client} (id={client_id})')

        if clear:
            from apps.sessions.models import SessionRun
            sr_deleted, _ = SessionRun.objects.filter(tpms_client_id=client_id).delete()
            prog_deleted, _ = Program.objects.filter(tpms_client_id=client_id, is_template=False).delete()
            self.stdout.write(f'  Cleared {prog_deleted} program(s) and {sr_deleted} session(s)')

        # ── Workflow templates ──────────────────────────────────────────────
        wf_objects = {}
        for wf_data in WORKFLOWS:
            wf, created = WorkflowTemplate.objects.get_or_create(
                name=wf_data['name'],
                defaults={
                    'description': wf_data['description'],
                    'phases': wf_data['phases'],
                },
            )
            wf_objects[wf_data['name']] = wf
            self.stdout.write(f'  {"Created" if created else "Found"} workflow: {wf.name}')

        default_wf = wf_objects.get('Standard DTT Workflow')
        behavior_wf = wf_objects.get('Behavior Reduction Workflow')

        # ── Prompting template ──────────────────────────────────────────────
        prompt_tpl, _ = PromptingTemplate.objects.get_or_create(
            name='Standard Prompt Hierarchy',
            defaults={
                'description': 'Full Physical → Partial Physical → Model → Gestural → Independent',
                'levels': [
                    {'label': 'Full Physical',    'score': 0, 'color': '#e74c3c', 'abbreviation': 'FP'},
                    {'label': 'Partial Physical', 'score': 0, 'color': '#e67e22', 'abbreviation': 'PP'},
                    {'label': 'Model',            'score': 0, 'color': '#f1c40f', 'abbreviation': 'M'},
                    {'label': 'Gestural',         'score': 0, 'color': '#3498db', 'abbreviation': 'G'},
                    {'label': 'Independent',      'score': 1, 'color': '#2ecc71', 'abbreviation': 'I'},
                ],
                'is_org_default': True,
            },
        )

        # ── Programs + Targets ──────────────────────────────────────────────
        total_programs = 0
        total_targets = 0

        for i, prog_data in enumerate(PROGRAMS):
            # Pick workflow
            if prog_data['category'] == 'behavior_reduction':
                wf = behavior_wf
            elif prog_data['category'] == 'abc_recording':
                wf = None
            else:
                wf = default_wf

            program = Program.objects.create(
                tpms_client_id=client_id,
                is_template=False,
                name=prog_data['name'],
                category=prog_data['category'],
                treatment_area=prog_data['treatment_area'],
                phase=prog_data['phase'],
                objective=prog_data['objective'],
                instructions=prog_data['instructions'],
                tags=prog_data['tags'],
                workflow_template=wf,
                status='active',
                display_order=i * 10,
            )
            total_programs += 1

            for j, t_data in enumerate(prog_data.get('targets', [])):
                use_prompt = t_data['measurement_type'] == 'discrete_trial'
                Target.objects.create(
                    program=program,
                    name=t_data['name'],
                    measurement_type=t_data['measurement_type'],
                    status=t_data['status'],
                    sd_text=t_data.get('sd_text', ''),
                    teaching_instructions='',
                    prompting_template=prompt_tpl if use_prompt else None,
                    is_visible_to_staff=t_data['status'] in ('probe', 'acquisition', 'mastered'),
                    display_order=j * 10,
                )
                total_targets += 1

            self.stdout.write(
                f'  Created program: "{program.name}" '
                f'({len(prog_data.get("targets", []))} targets)'
            )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {total_programs} programs, {total_targets} targets seeded for client {client_id}.'
        ))

        if seed_sessions:
            self._seed_sessions(client_id, staff, days)

    def _seed_sessions(self, client_id: int, staff, days: int):
        from apps.sessions.models import SessionRun, TrialEvent
        from apps.sessions.services import build_program_snapshot

        programs = list(
            Program.objects
            .filter(tpms_client_id=client_id, status='active', is_template=False)
            .prefetch_related('targets')
        )
        if not programs:
            self.stdout.write(self.style.WARNING('No active programs to seed sessions for.'))
            return

        # Build the program snapshot once
        snapshot = build_program_snapshot(client_id=client_id)

        now = datetime.now(tz=timezone.utc)
        total_sessions = 0
        total_trials = 0

        for day_offset in range(days, 0, -1):
            # Skip ~30% of days (weekends / cancellations feel realistic)
            if random.random() < 0.30:
                continue

            session_date = now - timedelta(days=day_offset)
            progress = 1 - (day_offset / days)   # 0.0 oldest → 1.0 newest

            session_start = session_date.replace(hour=9, minute=0, second=0, microsecond=0)
            session_end   = session_start + timedelta(hours=2)

            session = SessionRun.objects.create(
                tpms_client_id=client_id,
                staff=staff,
                status='approved',
                started_at=session_start,
                ended_at=session_end,
                submitted_at=session_end,
                reviewed_at=session_end + timedelta(minutes=20),
                program_snapshot=snapshot,
            )
            total_sessions += 1

            trial_events = []
            for program in programs:
                if program.category not in ('skill_acquisition',):
                    continue
                for target in program.targets.filter(
                    measurement_type__in=('discrete_trial', 'trial_by_trial'),
                    is_visible_to_staff=True,
                ):
                    # Accuracy improves over time: 40% → 90%, with per-target noise
                    base_acc = 0.40 + progress * 0.50
                    acc = max(0.05, min(1.0, base_acc + random.uniform(-0.15, 0.15)))
                    for trial_num in range(1, 11):
                        correct = random.random() < acc
                        trial_events.append(TrialEvent(
                            session_run=session,
                            target_id=target.id,
                            target_name=target.name,
                            trial_number=trial_num,
                            response_score=1 if correct else 0,
                            prompt_level_label='Independent' if correct else 'Full Physical',
                            recorded_at=session_start + timedelta(minutes=trial_num * 3),
                        ))

            TrialEvent.objects.bulk_create(trial_events)
            total_trials += len(trial_events)

        self.stdout.write(self.style.SUCCESS(
            f'Sessions — {total_sessions} sessions with {total_trials} trials seeded '
            f'across the last {days} days.'
        ))
