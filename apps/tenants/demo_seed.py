"""
Minimal demo dataset for a freshly self-signed-up free-trial organization:
one dummy staff user, one dummy client, a program with a couple of targets,
and a couple of approved sessions with trial data — enough for the new admin
to explore the product immediately. Must run inside schema_context(org.schema_name).

Deliberately smaller than apps.programs.management.commands.seed_org_demo_data
(which seeds a full Settings-page dataset for internal/dev use) — this only
needs to look populated, not exercise every Settings tab.
"""
from datetime import timedelta

from django.utils import timezone


def seed_demo_dataset(org, admin_user):
    from apps.accounts.models import User
    from apps.clients.models import Client, ClientStaffAssignment
    from apps.programs.models import Program, Target
    from apps.sessions.models import SessionRun, TrialEvent
    from apps.sessions.services import build_program_snapshot

    staff = User.objects.create_user(
        email=f'demo.staff+{org.schema_name}@progressly.io',
        first_name='Dana',
        last_name='(Demo Staff)',
        role=User.Role.STAFF,
        password=None,  # set_password(None) marks it unusable — demo account can't log in directly
        organization=admin_user.organization,
    )

    client = Client.objects.create(
        first_name='Jordan',
        last_name='(Demo Client)',
        preferred_name='Jordan',
        status=Client.Status.ACTIVE,
        intake_date=timezone.now().date(),
        created_by=admin_user,
    )
    ClientStaffAssignment.objects.create(client=client, user=staff, is_primary=True)

    program = Program.objects.create(
        external_client_id=client.id,
        name='Mand Training — Basic',
        category=Program.Category.SKILL_ACQUISITION,
        treatment_area='Communication',
        phase=Program.Phase.ACTIVE,
        objective='Client will independently request preferred items using vocal speech across 3 consecutive sessions with 80% accuracy.',
        instructions='Present the preferred item just out of reach. Wait 3-5 seconds for a spontaneous mand before prompting.',
        status=Program.Status.ACTIVE,
        display_order=0,
        created_by=admin_user,
    )
    targets = [
        Target.objects.create(
            program=program,
            name='Request preferred snack',
            measurement_type=Target.MeasurementType.DISCRETE_TRIAL,
            status='acquisition',
            sd_text='Present snack just out of reach, pause 5s',
            is_visible_to_staff=True,
            display_order=0,
            created_by=admin_user,
        ),
        Target.objects.create(
            program=program,
            name='Request preferred toy',
            measurement_type=Target.MeasurementType.DISCRETE_TRIAL,
            status='probe',
            sd_text='Hold toy visible but out of reach',
            is_visible_to_staff=True,
            display_order=1,
            created_by=admin_user,
        ),
    ]

    snapshot = build_program_snapshot(client_id=client.id)
    now = timezone.now()
    for day_offset in (2, 1):
        session_start = (now - timedelta(days=day_offset)).replace(hour=10, minute=0, second=0, microsecond=0)
        session_end = session_start + timedelta(hours=1)
        session = SessionRun.objects.create(
            external_client_id=client.id,
            staff=staff,
            status='approved',
            started_at=session_start,
            ended_at=session_end,
            submitted_at=session_end,
            reviewed_at=session_end + timedelta(minutes=15),
            program_snapshot=snapshot,
        )
        trial_events = []
        for target in targets:
            for trial_num in range(1, 6):
                is_correct = trial_num > 1 or day_offset == 1
                trial_events.append(TrialEvent(
                    organization_id=session.organization_id,
                    session_run=session,
                    target_id=target.id,
                    target_name=target.name,
                    trial_number=trial_num,
                    response_score=1 if is_correct else 0,
                    prompt_level_label='Independent' if is_correct else 'Full Physical',
                    recorded_at=session_start + timedelta(minutes=trial_num * 2),
                ))
        TrialEvent.objects.bulk_create(trial_events)

    return {'staff_id': staff.id, 'client_id': client.id, 'program_id': program.id}
