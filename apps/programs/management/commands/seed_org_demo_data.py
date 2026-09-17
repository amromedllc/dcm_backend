"""
Seeds a full demo dataset for one Organization: Settings-page entities
(treatment areas, tags, target statuses, data fields, prompting/mastery/
workflow templates, ABC categories), a demo Client, and Programs+Targets
for that client.

Written org-scoped from the start (tenant_context, not schema_context) —
this is what every seed command moves to once the M3 rework of the
schema-based ones lands; this one didn't exist before, so it started here
directly.

Usage:
    python manage.py seed_org_demo_data --org dev
    python manage.py seed_org_demo_data --org dev --clear
"""
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from apps.tenants.models import Organization
from shared.tenancy import tenant_context

SEED_STATUSES = [
    {'key': 'waiting', 'label': 'Waiting', 'icon': 'hourglass', 'color': '#94a3b8', 'is_staff_visible': False, 'is_default': True, 'display_order': 0},
    {'key': 'probe', 'label': 'Probe', 'icon': 'clipboard-list', 'color': '#d97706', 'is_staff_visible': True, 'is_default': False, 'display_order': 1},
    {'key': 'acquisition', 'label': 'Acquisition', 'icon': 'graduation-cap', 'color': '#2563eb', 'is_staff_visible': True, 'is_default': False, 'display_order': 2},
    {'key': 'mastered', 'label': 'Mastered', 'icon': 'trophy', 'color': '#7c3aed', 'is_staff_visible': True, 'is_default': False, 'display_order': 3},
    {'key': 'closed', 'label': 'Closed', 'icon': 'check-circle', 'color': '#059669', 'is_staff_visible': False, 'is_default': False, 'display_order': 4},
]

TREATMENT_AREAS = ['Communication', 'Language', 'Daily Living Skills', 'Behavior Management', 'Social Skills']

PROGRAM_TAGS = [
    {'name': 'Priority', 'color': '#dc2626'},
    {'name': 'New', 'color': '#2563eb'},
    {'name': 'Review', 'color': '#d97706'},
    {'name': 'Parent Training', 'color': '#7c3aed'},
]

DATA_FIELDS = [
    {'name': 'Insurance Authorization #', 'field_type': 'text', 'field_location': 'treatment_tab'},
    {'name': 'Re-eval Due Date', 'field_type': 'date', 'field_location': 'treatment_tab'},
    {'name': 'Parent Consent on File', 'field_type': 'yes_no', 'field_location': 'instructions_tab'},
]

PROMPTING_TEMPLATES = [
    {
        'name': 'Standard Prompt Hierarchy',
        'description': 'Full Physical → Partial Physical → Model → Gestural → Independent',
        'levels': [
            {'label': 'Full Physical', 'score': 0, 'color': '#e74c3c', 'abbreviation': 'FP'},
            {'label': 'Partial Physical', 'score': 0, 'color': '#e67e22', 'abbreviation': 'PP'},
            {'label': 'Model', 'score': 0, 'color': '#f1c40f', 'abbreviation': 'M'},
            {'label': 'Gestural', 'score': 0, 'color': '#3498db', 'abbreviation': 'G'},
            {'label': 'Independent', 'score': 1, 'color': '#2ecc71', 'abbreviation': 'I'},
        ],
        'is_org_default': True,
    },
    {
        'name': 'Errorless Teaching (3-Level)',
        'description': 'Model → Gestural → Independent — for skills taught with errorless learning.',
        'levels': [
            {'label': 'Model', 'score': 0, 'color': '#f1c40f', 'abbreviation': 'M'},
            {'label': 'Gestural', 'score': 0, 'color': '#3498db', 'abbreviation': 'G'},
            {'label': 'Independent', 'score': 1, 'color': '#2ecc71', 'abbreviation': 'I'},
        ],
        'is_org_default': False,
    },
    {
        'name': 'Verbal Prompt Hierarchy',
        'description': 'Full Verbal → Partial Verbal → Independent — for vocal/verbal-behavior targets.',
        'levels': [
            {'label': 'Full Verbal', 'score': 0, 'color': '#e74c3c', 'abbreviation': 'FV'},
            {'label': 'Partial Verbal', 'score': 0, 'color': '#e67e22', 'abbreviation': 'PV'},
            {'label': 'Independent', 'score': 1, 'color': '#2ecc71', 'abbreviation': 'I'},
        ],
        'is_org_default': False,
    },
]

ABC_CATEGORY_ITEMS = {
    'setting': ['Home', 'Classroom', 'Community', 'Therapy Room', 'Playground'],
    'reporter': ['BCBA', 'RBT', 'Parent/Caregiver', 'Teacher'],
}

WORKFLOWS = [
    {
        'name': 'Standard DTT Workflow',
        'description': 'Probe → Acquisition → Mastered progression for discrete trial training.',
        'phases': [
            {'phase': 'probe', 'criteria': {'consecutive_sessions': 1, 'threshold_pct': 100, 'minimum_trials': 3}, 'on_success': 'acquisition', 'on_regression': None},
            {'phase': 'acquisition', 'criteria': {'consecutive_sessions': 3, 'threshold_pct': 80, 'minimum_trials': 5}, 'on_success': 'mastered', 'on_regression': 'probe'},
            {'phase': 'mastered', 'criteria': {'consecutive_sessions': 2, 'threshold_pct': 90, 'minimum_trials': 5}, 'on_success': 'maintenance', 'on_regression': 'acquisition'},
        ],
        'is_org_default': True,
    },
    {
        'name': 'Behavior Reduction Workflow',
        'description': 'Tracks frequency/duration toward reduction goals.',
        'phases': [
            {'phase': 'baseline', 'criteria': {'consecutive_sessions': 3, 'threshold_pct': 0, 'minimum_trials': 1}, 'on_success': 'acquisition', 'on_regression': None},
            {'phase': 'acquisition', 'criteria': {'consecutive_sessions': 5, 'threshold_pct': 20, 'minimum_trials': 1}, 'on_success': 'mastered', 'on_regression': 'baseline'},
        ],
        'is_org_default': False,
    },
]

PROGRAMS = [
    {
        'name': 'Mand Training — Basic',
        'category': 'skill_acquisition',
        'treatment_area': 'Communication',
        'phase': 'teaching',
        'objective': 'Client will independently request preferred items, activities, and breaks using vocal speech or AAC device across 3 consecutive sessions with 80% accuracy.',
        'instructions': 'Use the PECS or vocal mand protocol. Present the preferred item just out of reach. Wait 3-5 seconds for a spontaneous mand before prompting. Reinforce immediately.',
        'tags': ['Communication'],
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
        'tags': ['New'],
        'targets': [
            {'name': 'Identify nose', 'measurement_type': 'discrete_trial', 'status': 'mastered', 'sd_text': 'Show me your nose'},
            {'name': 'Identify ears', 'measurement_type': 'discrete_trial', 'status': 'acquisition', 'sd_text': 'Show me your ears'},
            {'name': 'Identify eyes', 'measurement_type': 'discrete_trial', 'status': 'probe', 'sd_text': 'Show me your eyes'},
        ],
    },
    {
        'name': 'Tantrum Behavior',
        'category': 'behavior_reduction',
        'treatment_area': 'Behavior Management',
        'phase': 'teaching',
        'objective': 'Reduce duration of tantrum episodes to under 2 minutes per session average.',
        'instructions': 'Record start and end time of each tantrum. Use planned ignoring unless safety is a concern.',
        'tags': ['Priority'],
        'targets': [
            {'name': 'Tantrum duration', 'measurement_type': 'duration', 'status': 'acquisition', 'sd_text': 'Record total duration of tantrum episode in seconds'},
        ],
    },
]


ORG_PROGRAM_TEMPLATES = [
    {
        'name': 'Mand Training — Template',
        'category': 'skill_acquisition',
        'treatment_area': 'Communication',
        'phase': 'teaching',
        'objective': 'Standard mand-training template for facility-wide reuse — copy to a client and adjust targets as needed.',
        'instructions': 'Use the PECS or vocal mand protocol. Present the preferred item just out of reach and wait for a spontaneous mand before prompting.',
        'tags': ['Communication'],
        'targets': [
            {'name': 'Request preferred item', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Present item just out of reach, pause 5s'},
            {'name': 'Request break', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Present task demand, wait for mand'},
        ],
    },
    {
        'name': 'Receptive Identification — Template',
        'category': 'skill_acquisition',
        'treatment_area': 'Language',
        'phase': 'teaching',
        'objective': 'Generic receptive-ID template (colors, shapes, body parts, etc.) — swap in target-specific stimuli per client.',
        'instructions': 'Present 2-3 field array. Ask "Show me ___". Use errorless learning initially, fading prompts systematically.',
        'tags': ['New'],
        'targets': [
            {'name': 'Identify target 1', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Show me ___'},
            {'name': 'Identify target 2', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Show me ___'},
            {'name': 'Identify target 3', 'measurement_type': 'discrete_trial', 'status': 'waiting', 'sd_text': 'Show me ___'},
        ],
    },
    {
        'name': 'Behavior Reduction — Template',
        'category': 'behavior_reduction',
        'treatment_area': 'Behavior Management',
        'phase': 'baseline',
        'objective': 'Generic behavior-reduction template — define the target behavior operationally per client before assigning.',
        'instructions': 'Record frequency/duration per the assigned measurement type. Confirm function via FBA before implementing a reduction procedure.',
        'tags': ['Priority'],
        'targets': [
            {'name': 'Target behavior', 'measurement_type': 'frequency', 'status': 'waiting', 'sd_text': 'Record each occurrence'},
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed Settings-page data, a demo client, and programs/targets for one Organization'

    def add_arguments(self, parser):
        parser.add_argument('--org', required=True, help='Organization schema_name or slug (e.g. dev)')
        parser.add_argument('--clear', action='store_true', help='Delete this org\'s existing seeded programs/client first')
        parser.add_argument(
            '--created-by-email', default='antony@amromed.org',
            help='Owner stamped on every seeded settings row (Settings tabs are practice-scoped via created_by)',
        )

    def handle(self, *args, **options):
        org = self._resolve_org(options['org'])
        with schema_context(org.schema_name), tenant_context(org.pk):
            creator = self._resolve_creator(options['created_by_email'])
            self._seed(org, options['clear'], creator)

    def _resolve_creator(self, email: str):
        from apps.accounts.models import User
        try:
            return User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f'No User with email "{email}" — create one first or pass --created-by-email')

    def _owned(self, model, defaults=None, creator=None, **lookup):
        """get_or_create that always stamps/backfills created_by.

        Settings rows are practice-scoped via created_by (see _settings_qs in
        apps/programs/api.py) — a row seeded with created_by=None is invisible
        to every practice-scoped viewer except on TargetStatus. Backfilling on
        an already-existing row (not just at creation) matters because this
        command is meant to be re-run safely on orgs seeded before this existed.
        """
        obj, created = model.objects.get_or_create(defaults={**(defaults or {}), 'created_by': creator}, **lookup)
        if not created and obj.created_by_id is None and creator is not None:
            obj.created_by = creator
            obj.save(update_fields=['created_by'])
        return obj, created

    def _resolve_org(self, ref: str) -> Organization:
        try:
            return Organization.objects.get(schema_name=ref)
        except Organization.DoesNotExist:
            pass
        try:
            return Organization.objects.get(slug=ref)
        except Organization.DoesNotExist:
            raise CommandError(f'No Organization with schema_name or slug "{ref}"')

    def _seed(self, org: Organization, clear: bool, creator):
        from apps.clients.models import Client
        from apps.programs.models import (
            PromptingTemplate, Program, ProgramDataField,
            ProgramTag, Target, TargetStatus, TreatmentArea, WorkflowTemplate,
        )

        self.stdout.write(f'Seeding demo data for organization: {org.name} ({org.schema_name}), owner: {creator.email}')

        if clear:
            deleted, _ = Program.objects.filter(name__in=[p['name'] for p in PROGRAMS]).delete()
            self.stdout.write(f'  Cleared {deleted} previously-seeded program-tree row(s)')

        # ── Settings: statuses ──────────────────────────────────────────────
        for row in SEED_STATUSES:
            _, created = self._owned(TargetStatus, defaults=row, creator=creator, key=row['key'])
            self.stdout.write(f'  {"Created" if created else "Found"} status: {row["label"]}')

        # ── Settings: treatment areas ────────────────────────────────────────
        for name in TREATMENT_AREAS:
            self._owned(TreatmentArea, creator=creator, name=name)

        # ── Settings: tags ───────────────────────────────────────────────────
        tag_objects = {}
        for tag_data in PROGRAM_TAGS:
            tag, _ = self._owned(ProgramTag, defaults={'color': tag_data['color']}, creator=creator, name=tag_data['name'])
            tag_objects[tag_data['name']] = tag

        # ── Settings: data fields ───────────────────────────────────────────
        for field_data in DATA_FIELDS:
            self._owned(ProgramDataField, defaults=field_data, creator=creator, name=field_data['name'])

        # ── Settings: prompting templates ───────────────────────────────────
        prompting_objects = {}
        for tpl_data in PROMPTING_TEMPLATES:
            tpl, created = self._owned(
                PromptingTemplate,
                creator=creator,
                name=tpl_data['name'],
                defaults={
                    'description': tpl_data['description'],
                    'levels': tpl_data['levels'],
                    'is_org_default': tpl_data['is_org_default'],
                },
            )
            prompting_objects[tpl_data['name']] = tpl
            self.stdout.write(f'  {"Created" if created else "Found"} prompting template: {tpl.name}')

        prompt_tpl = prompting_objects.get('Standard Prompt Hierarchy')
        # ── Settings: ABC categories ─────────────────────────────────────────
        from apps.sessions.api import DEFAULT_ABC_CATEGORIES
        from apps.sessions.models import ABCCategory, ABCItem

        category_objects = {}
        for row in DEFAULT_ABC_CATEGORIES:
            category, created = self._owned(ABCCategory, defaults=row, creator=creator, key=row['key'])
            category_objects[row['key']] = category
            self.stdout.write(f'  {"Created" if created else "Found"} ABC category: {category.label}')

        for key, labels in ABC_CATEGORY_ITEMS.items():
            category = category_objects.get(key)
            if not category:
                continue
            for order, label in enumerate(labels):
                self._owned(
                    ABCItem, defaults={'display_order': order * 10}, creator=creator,
                    category=category, label=label,
                )

        # ── Settings: workflow templates ────────────────────────────────────
        workflow_objects = {}
        for wf_data in WORKFLOWS:
            wf, created = self._owned(
                WorkflowTemplate,
                creator=creator,
                name=wf_data['name'],
                defaults={
                    'description': wf_data['description'],
                    'phases': wf_data['phases'],
                    'is_org_default': wf_data['is_org_default'],
                },
            )
            workflow_objects[wf_data['name']] = wf
            self.stdout.write(f'  {"Created" if created else "Found"} workflow: {wf.name}')

        default_wf = workflow_objects.get('Standard DTT Workflow')
        behavior_wf = workflow_objects.get('Behavior Reduction Workflow')

        # ── Demo client ──────────────────────────────────────────────────────
        client, created = self._owned(
            Client, creator=creator,
            first_name='Jordan', last_name='Demo',
            defaults={'preferred_name': 'Jordan', 'status': 'active'},
        )
        self.stdout.write(f'  {"Created" if created else "Found"} client: {client.full_name} (id={client.id})')

        # ── Programs + targets ───────────────────────────────────────────────
        total_programs = 0
        total_targets = 0
        for i, prog_data in enumerate(PROGRAMS):
            if Program.objects.filter(name=prog_data['name'], external_client_id=client.id).exists():
                continue
            wf = behavior_wf if prog_data['category'] == 'behavior_reduction' else default_wf
            program = Program.objects.create(
                external_client_id=client.id,
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
                created_by=creator,
            )
            total_programs += 1
            for j, t_data in enumerate(prog_data['targets']):
                use_prompt = t_data['measurement_type'] == 'discrete_trial'
                Target.objects.create(
                    program=program,
                    name=t_data['name'],
                    measurement_type=t_data['measurement_type'],
                    status=t_data['status'],
                    sd_text=t_data.get('sd_text', ''),
                    prompting_template=prompt_tpl if use_prompt else None,
                    is_visible_to_staff=t_data['status'] in ('probe', 'acquisition', 'mastered'),
                    display_order=j * 10,
                    created_by=creator,
                )
                total_targets += 1
            self.stdout.write(f'  Created program: "{program.name}" ({len(prog_data["targets"])} targets)')

        # ── Org-level program library (/org-programs) ───────────────────────
        # Scoped by created_by.external_admin_id (apps/backend/apps/programs/api.py's
        # _org_qs), not by `organization` — so the creator must share the same
        # external_admin_id as whoever is logged in and viewing /org-programs.
        # `creator` already qualifies (see _org_qs — only external_admin_id matters,
        # not role), so reuse the same owner rather than looking up a separate admin.
        total_templates = 0
        total_template_targets = 0
        if creator.external_admin_id is None:
            self.stdout.write(self.style.WARNING(
                f'  Skipping org-program library — {creator.email} has no external_admin_id set.'
            ))
        else:
            for i, tpl_data in enumerate(ORG_PROGRAM_TEMPLATES):
                if Program.objects.filter(name=tpl_data['name'], is_template=True).exists():
                    continue
                wf = behavior_wf if tpl_data['category'] == 'behavior_reduction' else default_wf
                template = Program.objects.create(
                    is_template=True,
                    external_client_id=None,
                    name=tpl_data['name'],
                    category=tpl_data['category'],
                    treatment_area=tpl_data['treatment_area'],
                    phase=tpl_data['phase'],
                    objective=tpl_data['objective'],
                    instructions=tpl_data['instructions'],
                    tags=tpl_data['tags'],
                    workflow_template=wf,
                    status='active',
                    display_order=i * 10,
                    created_by=creator,
                )
                total_templates += 1
                for j, t_data in enumerate(tpl_data['targets']):
                    Target.objects.create(
                        program=template,
                        name=t_data['name'],
                        measurement_type=t_data['measurement_type'],
                        status=t_data['status'],
                        sd_text=t_data.get('sd_text', ''),
                        is_visible_to_staff=False,
                        display_order=j * 10,
                        created_by=creator,
                    )
                    total_template_targets += 1
                self.stdout.write(f'  Created org-program template: "{template.name}" ({len(tpl_data["targets"])} targets)')

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — org "{org.name}": {total_programs} programs, {total_targets} targets, '
            f'client id={client.id}, {total_templates} org-program template(s) with {total_template_targets} target(s), '
            f'plus Settings-page data (statuses/areas/tags/fields/templates/ABC categories).'
        ))
