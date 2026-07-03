"""
Seed realistic trial data for the last N days so the analytics chart
shows something meaningful during development.

Usage:
    python manage.py seed_trial_data --schema dev --days 30
    python manage.py seed_trial_data --schema dev --days 30 --clear
"""

import random
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.clients.models import Client
from apps.programs.models import Program, Target
from apps.sessions.models import SessionRun, TrialEvent
from apps.sessions.services import build_program_snapshot


class Command(BaseCommand):
    help = 'Seed fake session + trial data for development/testing'

    def add_arguments(self, parser):
        parser.add_argument('--schema', required=True, help='Tenant schema name')
        parser.add_argument('--days', type=int, default=30, help='How many past days to fill')
        parser.add_argument('--sessions-per-day', type=int, default=1, help='Sessions per day (default 1)')
        parser.add_argument('--trials-per-target', type=int, default=10, help='Trials per target per session')
        parser.add_argument('--clear', action='store_true', help='Delete existing seeded sessions and trial events first')

    def handle(self, *args, **options):
        with schema_context(options['schema']):
            self._seed(
                days=options['days'],
                sessions_per_day=options['sessions_per_day'],
                trials_per_target=options['trials_per_target'],
                clear=options['clear'],
            )

    def _seed(self, days, sessions_per_day, trials_per_target, clear):
        client = Client.objects.first()
        if not client:
            raise CommandError('No clients found. Create a client first.')

        staff = User.objects.filter(role__in=['admin', 'supervisor', 'therapist']).first()
        if not staff:
            raise CommandError('No staff user found.')

        programs = list(
            Program.objects
            .filter(client=client, status='active')
            .prefetch_related('targets__prompting_template')
        )
        if not programs:
            raise CommandError(f'No active programs for client "{client}".')

        all_targets = {
            p.id: list(p.targets.filter(is_visible_to_staff=True))
            for p in programs
        }

        if clear:
            sr_deleted, _ = SessionRun.objects.filter(client=client).delete()
            self.stdout.write(f'Cleared {sr_deleted} existing session(s) and their trial events.')

        # Build the snapshot once (same program config for all seeded sessions)
        snapshot = build_program_snapshot(client_id=client.id)

        now = datetime.now(tz=timezone.utc)
        total_sessions = 0
        total_trials = 0

        for day_offset in range(days, 0, -1):
            session_date = now - timedelta(days=day_offset)
            progress = 1 - (day_offset / days)   # 0.0 (oldest) → 1.0 (newest)

            for s_idx in range(sessions_per_day):
                session_start = session_date.replace(
                    hour=9 + s_idx * 4, minute=0, second=0, microsecond=0
                )
                session_end = session_start + timedelta(hours=2)

                session = SessionRun.objects.create(
                    client=client,
                    staff=staff,
                    status='approved',
                    started_at=session_start,
                    ended_at=session_end,
                    submitted_at=session_end,
                    reviewed_at=session_end + timedelta(minutes=30),
                    program_snapshot=snapshot,
                )
                total_sessions += 1

                trial_events = []
                for program in programs:
                    targets = all_targets.get(program.id, [])
                    for target in targets:
                        if target.measurement_type != 'trial_by_trial':
                            continue

                        # Gradually improving accuracy: 40% → 90%
                        base_accuracy = 0.40 + progress * 0.50
                        accuracy = max(0.05, min(1.0, base_accuracy + random.uniform(-0.15, 0.15)))

                        for trial_num in range(1, trials_per_target + 1):
                            is_correct = random.random() < accuracy
                            recorded_at = session_start + timedelta(minutes=trial_num * 2)
                            trial_events.append(TrialEvent(
                                session_run=session,
                                target_id=target.id,
                                target_name=target.name,
                                trial_number=trial_num,
                                response_score=1 if is_correct else 0,
                                prompt_level_label='Independent' if is_correct else 'Full Physical',
                                recorded_at=recorded_at,
                            ))

                TrialEvent.objects.bulk_create(trial_events)
                total_trials += len(trial_events)

        self.stdout.write(self.style.SUCCESS(
            f'Seeded {total_sessions} session(s) with {total_trials} trial events '
            f'across {len(programs)} program(s) for client "{client}" '
            f'over the last {days} days.'
        ))
