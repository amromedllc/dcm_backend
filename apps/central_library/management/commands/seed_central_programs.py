"""
Seed the platform's shared Central Library (CentralProgram/CentralTarget) —
public-schema, org-agnostic reference programs every organization can import
into their own Program/Target rows (see apps.programs.api._clone_central_program).

Reuses the same starter curriculum content as
apps.programs.management.commands.seed_client_programs, minus anything
client/progress-specific (status, workflow_template) since a central program
is a blank template, not a client's in-progress program.

Usage:
    python manage.py seed_central_programs
    python manage.py seed_central_programs --folder "Starter Curriculum"
    python manage.py seed_central_programs --clear
"""
from django.core.management.base import BaseCommand

from apps.central_library.models import CentralProgram, CentralProgramFolder, CentralTarget
from apps.programs.management.commands.seed_client_programs import PROGRAMS


class Command(BaseCommand):
    help = 'Seed the shared Central Library with starter programs and targets'

    def add_arguments(self, parser):
        parser.add_argument(
            '--folder', default=None,
            help='CentralProgramFolder name to file every seeded program under (created if missing)',
        )
        parser.add_argument(
            '--clear', action='store_true',
            help='Delete existing central programs with these names first',
        )

    def handle(self, *args, **options):
        folder = None
        if options['folder']:
            folder, created = CentralProgramFolder.objects.get_or_create(name=options['folder'])
            self.stdout.write(f'{"Created" if created else "Found"} folder: {folder.name}')

        if options['clear']:
            names = [row['name'] for row in PROGRAMS]
            deleted, _ = CentralProgram.objects.filter(name__in=names).delete()
            self.stdout.write(f'Cleared {deleted} existing central program row(s)')

        total_programs = 0
        total_targets = 0

        for i, prog_data in enumerate(PROGRAMS):
            program = CentralProgram.objects.create(
                name=prog_data['name'],
                category=prog_data['category'],
                phase=prog_data['phase'],
                treatment_area=prog_data['treatment_area'],
                objective=prog_data['objective'],
                instructions=prog_data['instructions'],
                tags=prog_data['tags'],
                folder=folder,
                display_order=i * 10,
            )
            total_programs += 1

            for j, t_data in enumerate(prog_data.get('targets', [])):
                CentralTarget.objects.create(
                    program=program,
                    name=t_data['name'],
                    measurement_type=t_data['measurement_type'],
                    sub_items=t_data.get('sub_items', []),
                    sd_text=t_data.get('sd_text', ''),
                    display_order=j * 10,
                )
                total_targets += 1

            self.stdout.write(
                f'  Created central program: "{program.name}" '
                f'({len(prog_data.get("targets", []))} targets)'
            )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {total_programs} central programs, {total_targets} central targets seeded.'
        ))
