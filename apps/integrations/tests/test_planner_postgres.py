"""Real migrations, row/schema isolation, SQL retention and populated upgrades."""
import uuid
import io
from contextlib import redirect_stdout
from datetime import date
from unittest import skipUnless
from types import SimpleNamespace

from django.db import connection, DatabaseError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.test import override_settings
from django_tenants.utils import schema_context

from apps.clients.models import TreatmentPlan
from apps.integrations.models import PlannerContentSnapshot
from apps.integrations.practice_planner.mock import MockPracticePlannerProvider
from apps.integrations.practice_planner.snapshots import retain_selection
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL test settings')
class PlannerPostgresTests(TransactionTestCase):
    def setUp(self):
        if not connection.settings_dict['NAME'].startswith('test_'):
            raise RuntimeError('Planner tests require a Django test database')
        self.orgs = []
        for index in range(2):
            schema = 'planner_test_' + uuid.uuid4().hex[:12]
            with redirect_stdout(io.StringIO()):
                org = Organization.objects.create(name=f'Synthetic {index}', slug=schema, schema_name=schema)
            self.orgs.append(org)
        self.content = MockPracticePlannerProvider().get_component('p1', edition='test-1')

    def tearDown(self):
        connection.set_schema_to_public()
        # Only schemas created above, in a guarded test_ database, are dropped.
        for org in self.orgs:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA {connection.ops.quote_name(org.schema_name)} CASCADE')
        # TenantMixin.delete() runs a cascade collector after dropping the schema,
        # which cannot resolve tenant tables. All test-owned tenant schemas are
        # gone, so delete only their empty shared organization records directly.
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM tenants_organizationtpmsadminid WHERE organization_id = ANY(%s)', [[o.pk for o in self.orgs]])
            cursor.execute('DELETE FROM tenants_organization WHERE id = ANY(%s)', [[o.pk for o in self.orgs]])

    @override_settings(DEBUG=True, PRACTICE_PLANNER_PROVIDER='mock')
    def test_shared_migration_defaults_and_request_schema_gate(self):
        from apps.accounts.models import User
        from apps.tenants.models import OrganizationTpmsAdminId
        from apps.integrations.practice_planner.access import availability_for_request
        org = self.orgs[0]
        with schema_context('public'):
            old_target = [('tenants', '0016_facility_last_client_provider_sync')]
            executor = MigrationExecutor(connection)
            latest = executor.loader.graph.leaf_nodes()
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            OldFacility = old_apps.get_model('tenants', 'OrganizationTpmsAdminId')
            old_row = OldFacility.objects.create(organization_id=org.pk, admin_id=909, ai_enabled=True)
            MigrationExecutor(connection).migrate(latest)
            facility = OrganizationTpmsAdminId.objects.get(pk=old_row.pk)
            self.assertFalse(facility.wiley_enabled)
            self.assertFalse(facility.wiley_entitled)
            self.assertTrue(facility.ai_enabled)
            facility.wiley_enabled = True
            facility.save(update_fields=['wiley_enabled'])
            user = User.objects.create(email='synthetic-pg-access@example.test', role='staff', external_admin_id=909)
            request = SimpleNamespace(user=user, tenant=org,
                _jwt_payload={'org_id': org.pk, 'sub': str(user.pk), 'type': 'access'})
            with tenant_context(org.pk):
                self.assertFalse(availability_for_request(request)['enabled'])
        with schema_context(org.schema_name), tenant_context(org.pk):
            self.assertTrue(availability_for_request(request)['enabled'])
            facility.wiley_enabled = False
            facility.save(update_fields=['wiley_enabled'])
            self.assertFalse(availability_for_request(request)['enabled'])

    def test_two_schemas_and_two_row_contexts_remain_isolated(self):
        a, b = self.orgs
        for org in self.orgs:
            with schema_context(org.schema_name), tenant_context(org.pk):
                selected = retain_selection(self.content)
                self.assertEqual(PlannerContentSnapshot.objects.count(), 1)
                with tenant_context(b.pk if org == a else a.pk):
                    self.assertFalse(PlannerContentSnapshot.objects.exists())
                with connection.cursor() as cursor:
                    cursor.execute('SELECT organization_id FROM integrations_plannercontentsnapshot')
                    self.assertEqual(cursor.fetchall(), [(org.pk,)])
                self.assertEqual(selected.organization_id, org.pk)

    def test_database_rejects_direct_update_delete_and_truncate_in_each_schema(self):
        for org in self.orgs:
            with schema_context(org.schema_name), tenant_context(org.pk):
                selected = retain_selection(self.content)
                for sql in (
                    'UPDATE integrations_plannercontentsnapshot SET text = text',
                    'DELETE FROM integrations_plannercontentsnapshot',
                    'TRUNCATE integrations_plannercontentsnapshot CASCADE',
                ):
                    with self.subTest(schema=org.schema_name, sql=sql):
                        with self.assertRaises(DatabaseError), transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute(sql)
                selected.refresh_from_db()
                self.assertEqual(selected.text, self.content.text)

    def test_upgrade_populated_legacy_plans_preserves_content_and_signature_hash(self):
        org = self.orgs[0]
        old_targets = [('clients', '0013_baseline_client_status_observations')]
        with schema_context(org.schema_name), tenant_context(org.pk):
            executor = MigrationExecutor(connection)
            latest = executor.loader.graph.leaf_nodes()
            executor.migrate(old_targets)
            historical = executor.loader.project_state(old_targets).apps
            OldClient = historical.get_model('clients', 'Client')
            OldPlan = historical.get_model('clients', 'TreatmentPlan')
            OldSignature = historical.get_model('clients', 'TreatmentPlanSignature')
            client = OldClient.objects.create(organization_id=org.pk, first_name='Synthetic', last_name='Upgrade')
            old_ids = []
            for index in range(3):
                plan = OldPlan.objects.create(organization_id=org.pk, client_id=client.pk, title=f'Synthetic old {index}',
                    plan_date=date(2026, 1, 1), sections={'original': index}, source_snapshot={'synthetic': True}, status='finalized')
                old_ids.append(plan.pk)
                OldSignature.objects.create(organization_id=org.pk, plan_id=plan.pk, signer_id=100, signer_name='Synthetic',
                    signer_role='supervisor', signature_data='Synthetic', content_hash='b' * 64)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            for index, plan in enumerate(TreatmentPlan.objects.filter(pk__in=old_ids).order_by('pk')):
                self.assertEqual(plan.sections, {'original': index})
                self.assertEqual(plan.source_snapshot, {'synthetic': True})
                self.assertEqual(plan.status, 'finalized')
                self.assertEqual(plan.signatures.get().content_hash, 'b' * 64)
                self.assertEqual(plan.planning_mode, 'analytics')
                self.assertEqual(plan.revision_number, 1)
            self.assertEqual(TreatmentPlan.objects.filter(pk__in=old_ids).values('plan_series_id').distinct().count(), 3)

    def test_existing_note_body_status_and_signature_survive_content_selection(self):
        from apps.notes.models import LessonNote, NoteSignature, NoteTemplate
        org = self.orgs[0]
        with schema_context(org.schema_name), tenant_context(org.pk):
            template = NoteTemplate.objects.create(name='Synthetic template', fields=[{'key': 'summary', 'required': True}])
            note = LessonNote.objects.create(template=template, note_date=date(2026, 1, 1),
                body={'summary': 'Preserved synthetic narrative'}, status='approved')
            signature = NoteSignature.objects.create(note=note, signer_id=100, signer_name='Synthetic', signer_role='supervisor',
                signature_type='supervisor', signature_data='Synthetic signature')
            for key in ('p1', 'g1', 'o1', 'i1', 'n1', 'h1'):
                retain_selection(MockPracticePlannerProvider().get_component(key, edition='test-1'))
            note.refresh_from_db()
            signature.refresh_from_db()
            self.assertEqual(note.body, {'summary': 'Preserved synthetic narrative'})
            self.assertEqual(note.status, 'approved')
            self.assertEqual(signature.signature_data, 'Synthetic signature')

    @override_settings(DEBUG=True, PRACTICE_PLANNER_PROVIDER='mock')
    def test_concurrent_editors_cannot_silently_overwrite_structured_draft(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from ninja.errors import HttpError
        from apps.accounts.models import User
        from apps.clients.models import Client
        from apps.clients.planner_api import create_draft, update_draft
        from apps.clients.planner_schemas import DraftInput, DraftUpdate
        from apps.tenants.models import OrganizationTpmsAdminId
        org = self.orgs[0]
        OrganizationTpmsAdminId.objects.create(organization=org, admin_id=910, wiley_enabled=True)
        user = User.objects.create(email='synthetic-concurrent@example.test', role='admin', external_admin_id=910)
        request = SimpleNamespace(user=user, tenant=org,
            _jwt_payload={'org_id': org.pk, 'sub': str(user.pk), 'type': 'access'})
        with schema_context(org.schema_name), tenant_context(org.pk):
            client = Client.objects.create(first_name='Synthetic', last_name='Concurrent', external_admin_id=910)
            _, plan = create_draft(request, client.pk, DraftInput(title='Synthetic concurrent draft', plan_date=date(2026, 1, 1)))
        barrier = Barrier(2)

        def edit(title):
            close_old_connections()
            try:
                with schema_context(org.schema_name), tenant_context(org.pk):
                    barrier.wait(timeout=10)
                    try:
                        result = update_draft(request, client.pk, plan['id'], DraftUpdate(title=title,
                            plan_date=date(2026, 1, 1), expected_lock_version=1))
                        return 200, result['title']
                    except HttpError as error:
                        return error.status_code, None
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ['Synthetic editor A', 'Synthetic editor B']))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        with schema_context(org.schema_name), tenant_context(org.pk):
            saved = TreatmentPlan.objects.get(pk=plan['id'])
            self.assertEqual(saved.lock_version, 2)
            self.assertEqual(saved.title, next(title for status, title in results if status == 200))
