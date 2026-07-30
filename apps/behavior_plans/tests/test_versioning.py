"""
BehaviorInterventionPlan versioning: only one ACTIVE row per *program* at a
time (not per client — a client can have several distinct behavior-reduction
programs, each needing its own independently-active plan), activating a new
version must auto-archive whatever was previously active for that same
program, and organization must derive from the program FK rather than
relying on ambient tenant context (see _derive_organization_id).
"""
from django.db import IntegrityError
from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.behavior_plans.models import BehaviorInterventionPlan
from apps.behavior_plans.services import (
    activate_bip_version, archive_bip_version, create_revision,
)
from apps.clients.models import Client
from apps.programs.models import Program
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class BehaviorInterventionPlanTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org BIP', slug='test-org-bip', schema_name='test_org_bip',
        )
        self.user = User.objects.create_user(
            email='bcba@example.com', password='x',
            first_name='BC', last_name='BA', organization=self.org,
        )

    def _make_client(self):
        return Client.objects.create(
            first_name='Kid', last_name='Test', organization=self.org,
        )

    def _make_program(self, client, name='Aggression Program'):
        return Program.objects.create(
            name=name,
            category=Program.Category.BEHAVIOR_REDUCTION,
            external_client_id=client.id,
            organization=self.org,
        )

    def _make_plan(self, program, **overrides):
        defaults = dict(
            program=program,
            version_number=1,
            author=self.user,
            author_name='BC BA',
            created_by=self.user,
            target_behavior_name='Aggression',
            target_behavior_definition='Hitting others with an open hand',
            antecedent_strategies='Offer choices proactively',
            replacement_behaviors='Request a break using PECS card',
            response_procedures='Block, redirect, offer break card',
        )
        defaults.update(overrides)
        return BehaviorInterventionPlan.objects.create(**defaults)

    def test_activate_archives_previous_active(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = self._make_client()
            program = self._make_program(client)
            v1 = self._make_plan(program)
            activate_bip_version(v1, self.user)

            v2 = create_revision(v1, self.user)
            activate_bip_version(v2, self.user)

            v1.refresh_from_db()
            v2.refresh_from_db()
            self.assertEqual(v1.status, BehaviorInterventionPlan.Status.ARCHIVED)
            self.assertEqual(v1.archive_reason, 'Superseded by version 2')
            self.assertEqual(v2.status, BehaviorInterventionPlan.Status.ACTIVE)
            self.assertEqual(
                BehaviorInterventionPlan.objects.filter(
                    program=program, status=BehaviorInterventionPlan.Status.ACTIVE,
                ).count(),
                1,
            )

    def test_cannot_have_two_active_plans_at_db_level(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = self._make_client()
            program = self._make_program(client)
            self._make_plan(program, status=BehaviorInterventionPlan.Status.ACTIVE)
            with self.assertRaises(IntegrityError):
                self._make_plan(
                    program, version_number=2,
                    status=BehaviorInterventionPlan.Status.ACTIVE,
                )

    def test_two_programs_same_client_can_both_be_active(self):
        """The actual point of the client->program rescope: two different
        problem behaviors for the same client each get their own
        independently-active plan, instead of contending for one slot."""
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = self._make_client()
            head_hitting = self._make_program(client, name='Self-Injurious Behavior')
            tantrums = self._make_program(client, name='Tantrum Behavior')

            plan_a = self._make_plan(head_hitting, target_behavior_name='Head hitting')
            plan_b = self._make_plan(tantrums, target_behavior_name='Tantrums')

            activate_bip_version(plan_a, self.user)
            activate_bip_version(plan_b, self.user)

            plan_a.refresh_from_db()
            plan_b.refresh_from_db()
            self.assertEqual(plan_a.status, BehaviorInterventionPlan.Status.ACTIVE)
            self.assertEqual(plan_b.status, BehaviorInterventionPlan.Status.ACTIVE)
            self.assertEqual(
                BehaviorInterventionPlan.objects.filter(
                    program__external_client_id=client.id,
                    status=BehaviorInterventionPlan.Status.ACTIVE,
                ).count(),
                2,
            )

    def test_revision_increments_version_and_links_previous(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = self._make_client()
            program = self._make_program(client)
            v1 = self._make_plan(program)
            v2 = create_revision(v1, self.user)

            self.assertEqual(v2.version_number, 2)
            self.assertEqual(v2.previous_version_id, v1.id)
            self.assertEqual(v2.status, BehaviorInterventionPlan.Status.DRAFT)
            self.assertEqual(v2.target_behavior_name, v1.target_behavior_name)

    def test_activate_rejects_missing_required_content(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = self._make_client()
            program = self._make_program(client)
            incomplete = self._make_plan(program, response_procedures='')
            with self.assertRaises(HttpError) as ctx:
                activate_bip_version(incomplete, self.user)
            self.assertEqual(ctx.exception.status_code, 422)

    def test_edit_archived_plan_raises_409(self):
        from apps.behavior_plans.services import _assert_editable

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = self._make_client()
            program = self._make_program(client)
            plan = self._make_plan(program)
            with self.assertRaises(HttpError) as ctx:
                archive_bip_version(plan, self.user, reason='n/a')  # not active yet
            self.assertEqual(ctx.exception.status_code, 409)

            activate_bip_version(plan, self.user)
            archive_bip_version(plan, self.user, reason='Discontinued')
            plan.refresh_from_db()
            self.assertEqual(plan.status, BehaviorInterventionPlan.Status.ARCHIVED)
            with self.assertRaises(HttpError) as ctx:
                _assert_editable(plan)
            self.assertEqual(ctx.exception.status_code, 409)

    def test_organization_derived_from_program(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = self._make_client()
            program = self._make_program(client)
            plan = self._make_plan(program)
            self.assertEqual(plan.organization_id, self.org.pk)
            self.assertEqual(plan.organization_id, program.organization_id)
            self.assertEqual(plan.client_id, client.id)
