"""
Seed a starter curriculum pack — real skill-acquisition programs/targets across the
core early-learner teaching domains (attending, receptive/expressive language, motor
imitation, matching, mand training, social play, self-care) plus behavior-reduction
and ABC programs — for a client, with workflow/fading/maintenance automation wired in.

Content here is authored in-house using standard, publicly-known DTT and task-analysis
teaching methodology (the kind covered in any RBT curriculum) — it does not reproduce
ESDM, SBT, or any other single copyrighted/trademarked curriculum's target bank verbatim.
Treat it as a generic starting pack, not a licensed replacement for one of those.

Usage:
    python manage.py seed_client_programs --schema dev --client-id 7616
    python manage.py seed_client_programs --schema dev --client-id 7616 --clear
    python manage.py seed_client_programs --schema dev --client-id 7616 --clear --seed-sessions --days 30
"""

import random
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from apps.programs.models import (
    Program, Target, WorkflowTemplate, PromptingTemplate,
    FadingTemplate, MaintenanceSchedule,
)
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


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

# Prompt-fading advancement/regression rules — applied to a target's current
# prompt-level index once fading_mode='automatic'. See FadingTemplate model.
FADING_TEMPLATES = [
    {
        'name': 'Standard Prompt Fading',
        'description': 'Advance one prompt level after 3 consecutive sessions ≥90%; regress on a sharp drop.',
        'rules': {
            'consecutive_sessions': 3,
            'threshold_pct': 90,
            'minimum_trials': 5,
            'regression_threshold_pct': 50,
        },
        'is_org_default': True,
    },
]

# Maintenance re-check cadence for mastered targets before final closure.
MAINTENANCE_SCHEDULES = [
    {
        'name': 'Standard Maintenance',
        'interval_type': 'every_n_sessions',
        'interval_value': 5,
        'episodes': 4,
        'success_threshold_pct': 80,
        'on_failure': 'back_to_acquisition',
        'is_org_default': True,
    },
]

PROGRAMS = [
    # ── Skill Acquisition — Attending & Compliance ──────────────────────────────
    {
        'name': 'Attending & Compliance — Sit and Attend',
        'category': 'skill_acquisition',
        'treatment_area': 'Attending Skills',
        'phase': 'teaching',
        'objective': 'Client will orient to instructor and remain seated for the duration of a 1:1 demand across 3 consecutive sessions with 90% accuracy.',
        'instructions': 'Deliver instruction only once client is seated and oriented. Reinforce brief compliance heavily at first, thinning the schedule as duration increases.',
        'tags': ['Foundational', 'Readiness'],
        'targets': [
            {'name': 'Sit in chair on request', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': '"Sit down"'},
            {'name': 'Orient to name (eye contact within 3s)', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Call client\'s name from 3 feet away'},
            {'name': 'Hands down / hands quiet', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Hands down"'},
        ],
    },
    # ── Skill Acquisition — Receptive Language ──────────────────────────────────
    {
        'name': 'Receptive Language — One-Step Instructions',
        'category': 'skill_acquisition',
        'treatment_area': 'Language',
        'phase': 'teaching',
        'objective': 'Client will correctly perform 5 one-step motor instructions when given the verbal SD alone, across 3 consecutive sessions with 80% accuracy.',
        'instructions': 'Present the instruction once; do not repeat. Use errorless teaching (model prompt) on new targets, fading to independent per the org fading template.',
        'tags': ['Language', 'Receptive'],
        'targets': [
            {'name': 'Come here', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': '"Come here"'},
            {'name': 'Stand up', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Stand up"'},
            {'name': 'Clap hands', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Clap your hands"'},
            {'name': 'Give me [item]', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': '"Give me the ___" (2 items on table)'},
            {'name': 'Wave bye-bye', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': '"Wave bye-bye"'},
        ],
    },
    {
        'name': 'Receptive Language — Body Parts',
        'category': 'skill_acquisition',
        'treatment_area': 'Language',
        'phase': 'teaching',
        'objective': 'Client will identify 6 body parts by pointing when asked "Show me ___" with 90% accuracy across 3 consecutive sessions.',
        'instructions': 'Use a card or doll for receptive identification. Mix targets across trials. Use errorless learning initially, fading prompts systematically per the fading template.',
        'tags': ['Language', 'Receptive'],
        'targets': [
            {'name': 'Identify nose', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': '"Show me your nose"'},
            {'name': 'Identify ears', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': '"Show me your ears"'},
            {'name': 'Identify eyes', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Show me your eyes"'},
            {'name': 'Identify hands', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Show me your hands"'},
            {'name': 'Identify feet', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': '"Show me your feet"'},
            {'name': 'Identify belly', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': '"Show me your belly"'},
        ],
    },
    {
        'name': 'Receptive Identification — Common Objects',
        'category': 'skill_acquisition',
        'treatment_area': 'Language',
        'phase': 'teaching',
        'objective': 'Client will point to 5 common objects when named, from an array of 3–5 items, with 90% accuracy across 3 consecutive sessions.',
        'instructions': 'Randomize item position each trial to control for side bias. Start with a 2-item field and expand as accuracy improves.',
        'tags': ['Language', 'Receptive'],
        'targets': [
            {'name': 'Point to ball', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Point to the ball" (array of 3)'},
            {'name': 'Point to cup', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Point to the cup" (array of 3)'},
            {'name': 'Point to shoe', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': '"Point to the shoe" (array of 3)'},
            {'name': 'Point to book', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': '"Point to the book" (array of 3)'},
        ],
    },
    # ── Skill Acquisition — Motor Imitation ──────────────────────────────────────
    {
        'name': 'Motor Imitation — Gross Motor Actions',
        'category': 'skill_acquisition',
        'treatment_area': 'Imitation',
        'phase': 'teaching',
        'objective': 'Client will imitate 4 gross motor actions within 5s of the model, with 90% accuracy across 3 consecutive sessions.',
        'instructions': '"Do this" + model the action. Use physical prompt on first exposure only, fading per the fading template. Vary action order across trials.',
        'tags': ['Imitation', 'Motor'],
        'targets': [
            {'name': 'Imitate clap hands', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': '"Do this" + clap'},
            {'name': 'Imitate raise arms', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Do this" + raise arms overhead'},
            {'name': 'Imitate stomp feet', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Do this" + stomp feet'},
            {'name': 'Imitate touch head', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': '"Do this" + touch head'},
        ],
    },
    # ── Skill Acquisition — Visual Performance / Matching ────────────────────────
    {
        'name': 'Visual Performance — Matching Identical Items',
        'category': 'skill_acquisition',
        'treatment_area': 'Visual Performance',
        'phase': 'teaching',
        'objective': 'Client will match identical objects/pictures to sample from a field of 3, with 90% accuracy across 3 consecutive sessions.',
        'instructions': 'Present sample, then array of 3 (1 match, 2 distractors). Reinforce independent matches; use gestural prompt only if needed.',
        'tags': ['Matching', 'Visual Performance'],
        'targets': [
            {'name': 'Match identical picture cards', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Match" (sample card + array of 3)'},
            {'name': 'Match identical objects', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': '"Match" (sample object + array of 3)'},
            {'name': 'Sort by color', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': '"Put the ones that are the same color together"'},
        ],
    },
    # ── Skill Acquisition — Expressive Language (Tacting) ────────────────────────
    {
        'name': 'Expressive Language — Labeling Common Objects',
        'category': 'skill_acquisition',
        'treatment_area': 'Language',
        'phase': 'teaching',
        'objective': 'Client will vocally label 5 common objects/pictures when shown, with 80% accuracy across 3 consecutive sessions.',
        'instructions': 'Hold up item, "What is this?" Wait 3–5s before prompting with an echoic model. Accept approximations per the client\'s current articulation goals.',
        'tags': ['Language', 'Expressive', 'Tacting'],
        'targets': [
            {'name': 'Label ball', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"What is this?" (holding ball)'},
            {'name': 'Label cup', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"What is this?" (holding cup)'},
            {'name': 'Label dog (picture)', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': '"What is this?" (picture card)'},
            {'name': 'Label shoe', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': '"What is this?" (holding shoe)'},
        ],
    },
    # ── Skill Acquisition — Mand Training (Functional Communication) ────────────
    {
        'name': 'Mand Training — Basic',
        'category': 'skill_acquisition',
        'treatment_area': 'Communication',
        'phase': 'teaching',
        'objective': 'Client will independently request preferred items, activities, and breaks using vocal speech or AAC device across 3 consecutive sessions with 80% accuracy.',
        'instructions': 'Present the preferred item just out of reach. Wait 3–5 seconds for a spontaneous mand before prompting. Reinforce immediately and specifically (give the item requested).',
        'tags': ['Communication', 'Verbal Behavior'],
        'targets': [
            {'name': 'Request preferred snack', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Present snack just out of reach, pause 5s'},
            {'name': 'Request break', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Present task demand, wait for mand'},
            {'name': 'Request preferred toy', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': 'Hold toy visible but out of reach'},
            {'name': 'Request help', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Present difficult task, wait for "help" mand'},
        ],
    },
    # ── Skill Acquisition — Social / Play Skills ─────────────────────────────────
    {
        'name': 'Social Play Skills — Turn-Taking & Joint Attention',
        'category': 'skill_acquisition',
        'treatment_area': 'Social Skills',
        'phase': 'teaching',
        'objective': 'Client will engage in reciprocal turn-taking and respond to bids for joint attention across 3 consecutive sessions with 80% accuracy.',
        'instructions': 'Embed within preferred play (ball rolling, blocks). Model the turn-taking exchange; prompt physically only if needed, fading per the fading template.',
        'tags': ['Social Skills', 'Play'],
        'targets': [
            {'name': 'Take turns rolling ball', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': '"Your turn" (ball rolling exchange)'},
            {'name': 'Respond to name call with eye contact + orient', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': 'Call name from across the room'},
            {'name': 'Point to share interest (protodeclarative)', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Novel/interesting item appears in the room'},
        ],
    },
    # ── Skill Acquisition — Self-Care / Daily Living (Task Analysis) ────────────
    {
        'name': 'Self-Care — Hand Washing',
        'category': 'skill_acquisition',
        'treatment_area': 'Daily Living Skills',
        'phase': 'teaching',
        'objective': 'Client will independently complete the hand-washing chain, scored step-by-step, with 90% of steps correct across 3 consecutive sessions.',
        'instructions': 'Use a visual task analysis posted at the sink. Provide the least intrusive prompt needed per step; score each step independently. Reinforce at the end of the full chain.',
        'tags': ['ADL', 'Independence', 'Task Analysis'],
        'is_task_analysis': True,
        'targets': [
            {
                'name': 'Hand-washing routine',
                'measurement_type': 'task_analysis',
                'status': 'acquisition',
                'sd_text': '"Go wash your hands"',
                'sub_items': [
                    {'key': 'turn_on_water', 'label': 'Turn on water'},
                    {'key': 'wet_hands', 'label': 'Wet hands'},
                    {'key': 'apply_soap', 'label': 'Apply soap'},
                    {'key': 'scrub_20s', 'label': 'Scrub for 20 seconds'},
                    {'key': 'rinse_hands', 'label': 'Rinse hands'},
                    {'key': 'dry_with_towel', 'label': 'Dry with towel'},
                ],
            },
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
            try:
                org = Organization.objects.get(schema_name=options['schema'])
            except Organization.DoesNotExist:
                raise CommandError(f'No Organization with schema_name "{options["schema"]}"')
            with tenant_context(org.pk):
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
            sr_deleted, _ = SessionRun.objects.filter(external_client_id=client_id).delete()
            prog_deleted, _ = Program.objects.filter(external_client_id=client_id, is_template=False).delete()
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

        # ── Fading template + maintenance schedule ──────────────────────────
        # Curriculum-pack targets that use the prompt hierarchy get fading/maintenance
        # automation wired in, so evaluate_session_fading()/mastery run for real instead
        # of sitting unused behind manual toggles.
        fading_tpl, _ = FadingTemplate.objects.get_or_create(
            name=FADING_TEMPLATES[0]['name'],
            defaults={
                'description': FADING_TEMPLATES[0]['description'],
                'rules': FADING_TEMPLATES[0]['rules'],
                'is_org_default': FADING_TEMPLATES[0]['is_org_default'],
            },
        )
        maintenance_sched, _ = MaintenanceSchedule.objects.get_or_create(
            name=MAINTENANCE_SCHEDULES[0]['name'],
            defaults={
                'interval_type': MAINTENANCE_SCHEDULES[0]['interval_type'],
                'interval_value': MAINTENANCE_SCHEDULES[0]['interval_value'],
                'episodes': MAINTENANCE_SCHEDULES[0]['episodes'],
                'success_threshold_pct': MAINTENANCE_SCHEDULES[0]['success_threshold_pct'],
                'on_failure': MAINTENANCE_SCHEDULES[0]['on_failure'],
                'is_org_default': MAINTENANCE_SCHEDULES[0]['is_org_default'],
            },
        )

        # ── Programs + Targets ──────────────────────────────────────────────
        total_programs = 0
        total_targets = 0

        for i, prog_data in enumerate(PROGRAMS):
            is_skill_acquisition = prog_data['category'] == 'skill_acquisition'
            if prog_data['category'] == 'behavior_reduction':
                wf = behavior_wf
            elif prog_data['category'] == 'abc_recording':
                wf = None
            else:
                wf = default_wf

            program = Program.objects.create(
                external_client_id=client_id,
                is_template=False,
                name=prog_data['name'],
                category=prog_data['category'],
                treatment_area=prog_data['treatment_area'],
                phase=prog_data['phase'],
                objective=prog_data['objective'],
                instructions=prog_data['instructions'],
                tags=prog_data['tags'],
                workflow_template=wf,
                fading_template=fading_tpl if is_skill_acquisition else None,
                maintenance_schedule=maintenance_sched if is_skill_acquisition else None,
                status='active',
                display_order=i * 10,
            )
            total_programs += 1

            for j, t_data in enumerate(prog_data.get('targets', [])):
                uses_prompt_hierarchy = t_data['measurement_type'] in ('discrete_trial', 'task_analysis')
                Target.objects.create(
                    program=program,
                    name=t_data['name'],
                    measurement_type=t_data['measurement_type'],
                    sub_items=t_data.get('sub_items', []),
                    status=t_data['status'],
                    sd_text=t_data.get('sd_text', ''),
                    teaching_instructions='',
                    prompting_template=prompt_tpl if uses_prompt_hierarchy else None,
                    workflow_template=wf,
                    fading_template=fading_tpl if uses_prompt_hierarchy else None,
                    maintenance_schedule=maintenance_sched if is_skill_acquisition else None,
                    mastery_mode='automatic' if is_skill_acquisition else 'manual',
                    fading_mode='automatic' if uses_prompt_hierarchy else 'manual',
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
            .filter(external_client_id=client_id, status='active', is_template=False)
            .prefetch_related('targets')
        )
        if not programs:
            self.stdout.write(self.style.WARNING('No active programs to seed sessions for.'))
            return

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
                external_client_id=client_id,
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
                            organization_id=session.organization_id,  # bulk_create bypasses save()'s auto-stamp
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
