"""Policy, query-boundary and decimal arithmetic regression tests."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from django.utils import timezone
from ninja.errors import HttpError
from pydantic import ValidationError
from apps.sessions import supervision_api as api
from apps.sessions.models import SupervisionContact
from shared.tenancy import CrossOrganizationReferenceError, tenant_context


class SupervisionTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(pk=1, id=1, organization_id=10, external_admin_id=100,
                                    role='supervisor', is_authenticated=True)
        self.request = SimpleNamespace(user=self.user)
        self.month = date(2026, 1, 1)

    def payload(self, **kwargs):
        return api.ContactInput(**dict(dict(rbt_id=2, supervisor_id=1, date=date(2026, 1, 5),
                                            hours='1.25', qualifications_confirmed=True), **kwargs))

    def test_role_rejections(self):
        for role in ['caregiver', 'reporting', 'superadmin', 'staff']:
            self.user.role = role
            calls = [(api.create_contact, (self.payload(),)), (api.people, ()),
                     (api.service_hours, (api.ServiceHoursInput(rbt_id=2, month=self.month, hours=10),)),
                     (api.void_contact, (1, api.VoidInput(reason='Correction')))]
            if role != 'staff':
                calls += [(api.report, (self.month,)), (api.contacts, (self.month,)), (api.report_csv, (self.month,))]
            for endpoint, args in calls:
                with self.subTest(role=role, endpoint=endpoint.__name__), self.assertRaises(HttpError) as error:
                    endpoint(self.request, *args)
                self.assertEqual(error.exception.status_code, 403)

    def test_shared_users_require_organization_and_practice(self):
        with tenant_context(10):
            sql, params = api._people(self.request).query.sql_with_params()
        self.assertIn('organization_id', sql)
        self.assertIn('external_admin_id', sql)
        self.assertIn(10, params)
        self.assertIn(100, params)

    def test_staff_reads_filter_self_and_organization(self):
        self.user.role = 'staff'
        with tenant_context(10):
            for qs in [api._rbts(self.request), api._contacts(self.request)]:
                sql, params = qs.query.sql_with_params()
                self.assertIn(10, params)
                self.assertIn(1, params)
                self.assertIn('organization_id', sql)
            sql, _ = api._contacts(self.request).query.sql_with_params()
        self.assertIn('rbt_id', sql)
        self.assertIn('supervisor_id', sql)
        self.assertIn(' IN ', sql)

    def test_related_staff_cannot_cross_organization(self):
        row = SupervisionContact(organization_id=10, rbt_id=2, supervisor_id=1)
        row._state.fields_cache['rbt'] = SimpleNamespace(organization_id=11)
        with self.assertRaises(CrossOrganizationReferenceError):
            row._validate_cross_org_fks()

    def test_cross_boundary_or_inactive_person_is_404(self):
        with patch.object(api, '_people') as people:
            people.return_value.filter.return_value.first.return_value = None
            with self.assertRaises(HttpError) as error:
                api._person(self.request, 999, ['staff'])
            self.assertEqual(error.exception.status_code, 404)
            people.return_value.filter.assert_called_once_with(pk=999, role__in=['staff'], is_active=True)

    def test_input_validation(self):
        for hours in ['0', '-1', '24.01', '1.234', 'NaN', 'Infinity']:
            with self.subTest(hours=hours), self.assertRaises(ValidationError):
                self.payload(hours=hours)
        with self.assertRaises(ValidationError):
            api.ServiceHoursInput(rbt_id=2, month=date(2026, 1, 2), hours=10)
        self.assertEqual(api._month(date(2024, 2, 1)), date(2024, 2, 29))
        with self.assertRaises(HttpError):
            api._month(date(2026, 1, 2))

    @patch.object(api, '_person')
    def test_ineligible_contacts_rejected(self, person):
        create = api.create_contact.__wrapped__.__wrapped__
        for data, code in [(self.payload(supervisor_id=3), 403),
                           (self.payload(date=timezone.localdate() + timedelta(days=1)), 400),
                           (self.payload(face_to_face=False), 400),
                           (self.payload(qualifications_confirmed=False), 400)]:
            with self.subTest(data=data), self.assertRaises(HttpError) as error:
                create(self.request, data)
            self.assertEqual(error.exception.status_code, code)

    def _report(self, service, contacts):
        service_qs = [] if service is None else [SimpleNamespace(rbt_id=2, hours=Decimal(service))]
        with patch.object(api.SupervisionServiceHours, 'objects') as hours, patch.object(api, '_contacts') as ledger, patch.object(api, '_rbts') as rbts:
            hours.filter.return_value = service_qs
            ledger.return_value.filter.return_value = contacts
            rbts.return_value = [SimpleNamespace(pk=2, full_name='Synthetic RBT')]
            result = api.report(self.request, self.month)[0]
            ledger.return_value.filter.assert_called_once_with(date__range=(self.month, date(2026, 1, 31)), void_reason='', qualifications_confirmed=True, face_to_face=True)
            return result

    def contact(self, hours, individual=True, observed=True):
        return SimpleNamespace(rbt_id=2, hours=Decimal(hours), individual=individual, observed_services=observed)

    def test_five_percent_and_contacts_pass(self):
        row = self._report('100', [self.contact('2.50'), self.contact('2.50')])
        self.assertEqual(row['percentage'], Decimal('5.00'))
        self.assertEqual(row['remaining_hours'], 0)
        self.assertTrue(row['recorded_requirements_met'])

    def test_rounded_percentage_never_grants_pass(self):
        row = self._report('100.01', [self.contact('2.50'), self.contact('2.50')])
        self.assertEqual(row['percentage'], Decimal('5.00'))
        self.assertGreater(row['remaining_hours'], 0)
        self.assertFalse(row['recorded_requirements_met'])

    def test_incomplete_reports_do_not_pass(self):
        for service, contacts in [(None, []), ('0', []), ('100', [self.contact('5')]),
                                  ('100', [self.contact('3', False), self.contact('3', False)]),
                                  ('100', [self.contact('3', observed=False), self.contact('3', observed=False)]),
                                  ('100', [self.contact('1'), self.contact('1')])]:
            with self.subTest(service=service, contacts=contacts):
                self.assertFalse(self._report(service, contacts)['recorded_requirements_met'])
        self.assertIsNone(self._report(None, [])['percentage'])
        self.assertIsNone(self._report('0', [])['percentage'])

    def test_csv_formula_escape_and_cache_policy(self):
        row = self._report('100', [])
        row['rbt_name'] = '=synthetic'
        with patch.object(api, '_reports', return_value=[row]):
            response = api.report_csv(self.request, self.month)
        self.assertIn("'=synthetic", response.content.decode())
        self.assertEqual(response['Cache-Control'], 'no-store')


    @patch.object(api, '_person')
    def test_contact_creation_uses_validated_payload_and_actor(self, person):
        data = self.payload()
        with patch.object(api.SupervisionContact, 'objects') as manager, patch.object(api, '_contact_output', return_value={'id': 7}):
            status, output = api.create_contact.__wrapped__.__wrapped__(self.request, data)
            manager.create.assert_called_once_with(**data.dict(), created_by=self.user)
            self.assertEqual(status, 201)
            self.assertEqual(output, {'id': 7})

    @patch.object(api, '_person')
    def test_service_total_update_preserves_original_creator(self, person):
        data = api.ServiceHoursInput(rbt_id=2, month=self.month, hours='100')
        with patch.object(api.SupervisionServiceHours, 'objects') as manager:
            api.service_hours.__wrapped__.__wrapped__(self.request, data)
            manager.update_or_create.assert_called_once_with(
                rbt_id=2, month=self.month, defaults={'hours': Decimal('100')},
                create_defaults={'hours': Decimal('100'), 'created_by': self.user})

    @patch.object(api, '_person')
    def test_short_month_rejects_impossible_service_hours(self, person):
        data = api.ServiceHoursInput(rbt_id=2, month=date(2026, 2, 1), hours='700')
        with self.assertRaises(HttpError) as error:
            api.service_hours.__wrapped__.__wrapped__(self.request, data)
        self.assertEqual(error.exception.status_code, 400)

    def test_void_retains_record_and_prevents_repeat_or_other_supervisor(self):
        row = SimpleNamespace(supervisor_id=1, void_reason='', save=Mock())
        with patch.object(api, '_contacts') as contacts, patch.object(api, '_contact_output', return_value={'id': 7}):
            contacts.return_value.select_for_update.return_value.filter.return_value.first.return_value = row
            void = api.void_contact.__wrapped__.__wrapped__
            self.assertEqual(void(self.request, 7, api.VoidInput(reason=' Correction ')), {'id': 7})
            self.assertEqual(row.void_reason, 'Correction')
            row.save.assert_called_once_with(update_fields=['void_reason', 'updated_at'])
            with self.assertRaises(HttpError) as error:
                void(self.request, 7, api.VoidInput(reason='Correction'))
            self.assertEqual(error.exception.status_code, 409)
            row.supervisor_id = 9
            row.void_reason = ''
            with self.assertRaises(HttpError) as error:
                void(self.request, 7, api.VoidInput(reason='Correction'))
            self.assertEqual(error.exception.status_code, 403)
