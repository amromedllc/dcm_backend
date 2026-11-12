from types import SimpleNamespace
from unittest import mock

from django.http import HttpResponse
from django.test import SimpleTestCase
from ninja.errors import HttpError
from pydantic import ValidationError

from apps.integrations.api import get_form_builder_settings, docuseal_sso_url, get_old_assigned_notes
from apps.integrations.form_builders import form_builder_mode, require_form_builder
from apps.tenants.api import update_superadmin_facility_form_builder
from apps.tenants.schemas import FacilityFormBuilderUpdate


class FormBuilderPolicyTests(SimpleTestCase):
    def request(self, **user):
        return SimpleNamespace(user=SimpleNamespace(external_admin_id=702, organization_id=None, **user),
                               tenant=SimpleNamespace(pk=9))

    def test_facility_policy_uses_verified_request_tenant_not_nullable_user_org(self):
        with mock.patch('apps.integrations.form_builders.OrganizationTpmsAdminId.objects.filter') as query:
            query.return_value.values_list.return_value.first.return_value = 'both'
            self.assertEqual(form_builder_mode(self.request()), 'both')
        query.assert_called_once_with(organization_id=9, admin_id=702)

    def test_another_tenants_mapping_cannot_grant_access(self):
        with mock.patch('apps.integrations.form_builders.OrganizationTpmsAdminId.objects.filter') as query:
            query.return_value.values_list.return_value.first.return_value = None
            for builder in ('new', 'old'):
                with self.assertRaises(HttpError) as error:
                    require_form_builder(self.request(), builder)
                self.assertEqual(error.exception.status_code, 403)

    def test_each_mode_grants_only_selected_builders(self):
        for mode in ('new', 'old', 'both', None):
            with self.subTest(mode=mode), mock.patch('apps.integrations.form_builders.form_builder_mode', return_value=mode):
                for builder in ('new', 'old'):
                    if mode in (builder, 'both'):
                        require_form_builder(self.request(), builder)
                    else:
                        with self.assertRaises(HttpError):
                            require_form_builder(self.request(), builder)

    def test_native_accounts_preserve_new_builder_only(self):
        request = SimpleNamespace(user=SimpleNamespace(external_admin_id=None))
        self.assertEqual(form_builder_mode(request), 'new')

    def test_features_fail_closed_when_mapping_is_missing(self):
        with (mock.patch('apps.integrations.api.require_permission'),
              mock.patch('apps.integrations.form_builders.form_builder_mode', return_value=None)):
            self.assertEqual(get_form_builder_settings(self.request()), {
                'mode': None, 'new_enabled': False, 'old_enabled': False,
            })

    def test_disabled_new_builder_rejects_direct_launch_before_sso(self):
        with (
            mock.patch('apps.integrations.api.require_permission'),
            mock.patch('apps.integrations.form_builders.form_builder_mode', return_value='old'),
            mock.patch('apps.integrations.api.build_sso_redirect_url') as launch,
        ):
            with self.assertRaises(HttpError):
                docuseal_sso_url(self.request())
        launch.assert_not_called()

    def test_only_superadmin_can_change_policy(self):
        with self.assertRaises(HttpError) as error:
            update_superadmin_facility_form_builder(self.request(is_superuser=False), 31, FacilityFormBuilderUpdate(form_builder_mode='both'))
        self.assertEqual(error.exception.status_code, 403)

    def test_policy_change_updates_only_selected_mapping_and_is_audited(self):
        facility = SimpleNamespace(pk=31, organization_id=9, admin_id=702, form_builder_mode='new', save=mock.Mock())
        request = self.request(pk=8, is_superuser=True)
        with (
            mock.patch('apps.tenants.api._superadmin_integration_facility', return_value=facility) as select,
            mock.patch('apps.tenants.api._serialize_facility_integration', return_value={'form_builder_mode': 'both'}),
            mock.patch('shared.audit.log_event') as audit,
        ):
            result = update_superadmin_facility_form_builder(request, 31, FacilityFormBuilderUpdate(form_builder_mode='both'))
        select.assert_called_once_with(request, 31)
        self.assertEqual(result['form_builder_mode'], 'both')
        self.assertEqual(facility.form_builder_mode, 'both')
        facility.save.assert_called_once_with(update_fields=['form_builder_mode', 'updated_at'])
        audit.assert_called_once()

    def test_invalid_modes_are_rejected_by_request_schema(self):
        with self.assertRaises(ValidationError):
            FacilityFormBuilderUpdate(form_builder_mode='unknown')

    def test_old_endpoint_requires_template_permission_before_remote_call(self):
        with (
            mock.patch('apps.integrations.api.require_permission', side_effect=HttpError(403, 'Permission denied')),
            mock.patch('apps.integrations.assigned_notes_client.fetch_assigned_session_notes') as fetch,
        ):
            with self.assertRaises(HttpError):
                get_old_assigned_notes(self.request(), HttpResponse())
        fetch.assert_not_called()

    def test_old_endpoint_rejects_disabled_builder_before_remote_call(self):
        with (
            mock.patch('apps.integrations.api.require_permission'),
            mock.patch('apps.integrations.form_builders.form_builder_mode', return_value='new'),
            mock.patch('apps.integrations.assigned_notes_client.fetch_assigned_session_notes') as fetch,
        ):
            with self.assertRaises(HttpError):
                get_old_assigned_notes(self.request(), HttpResponse())
        fetch.assert_not_called()

    def test_old_endpoint_uses_authenticated_identity_and_prevents_response_caching(self):
        request = self.request(external_employee_id=501)
        response = HttpResponse()
        notes = [{'id': 123, 'name': 'Synthetic', 'url': 'https://forms.example.test/123'}]
        with (
            mock.patch('apps.integrations.api.require_permission'),
            mock.patch('apps.integrations.form_builders.form_builder_mode', return_value='old'),
            mock.patch('apps.integrations.assigned_notes_client.fetch_assigned_session_notes', return_value=notes) as fetch,
        ):
            self.assertEqual(get_old_assigned_notes(request, response), notes)
        fetch.assert_called_once_with(request.user)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_old_endpoint_reports_missing_configuration_as_actionable_error(self):
        from apps.integrations.assigned_notes_client import AssignedNotesError
        with (
            mock.patch('apps.integrations.api.require_permission'),
            mock.patch('apps.integrations.form_builders.form_builder_mode', return_value='old'),
            mock.patch('apps.integrations.assigned_notes_client.fetch_assigned_session_notes', side_effect=AssignedNotesError('Old Form Builder is not configured.', 503)),
        ):
            with self.assertRaises(HttpError) as error:
                get_old_assigned_notes(self.request(), HttpResponse())
        self.assertEqual(error.exception.status_code, 503)

    def test_old_endpoint_route_injects_response_without_a_query_parameter(self):
        from ninja import Router
        from ninja.testing import TestClient
        test_router = Router()
        test_router.get('/assigned-notes', response=list[dict])(get_old_assigned_notes)
        with (
            mock.patch('apps.integrations.api.require_permission'),
            mock.patch('apps.integrations.form_builders.form_builder_mode', return_value='old'),
            mock.patch('apps.integrations.assigned_notes_client.fetch_assigned_session_notes', return_value=[]),
        ):
            response = TestClient(test_router).get('/assigned-notes')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
