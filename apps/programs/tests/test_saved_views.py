"""
SavedTableView — visibility resolution (private / everyone / roles) for the
Programs table's "Save Preferred Views" feature.
"""
from types import SimpleNamespace

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.programs.api import _visible_saved_views_qs
from apps.programs.models import SavedTableView
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


def _request_for(user):
    return SimpleNamespace(user=user)


class SavedViewVisibilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-saved-views', schema_name='test_org_saved_views',
        )
        self.owner = User.objects.create_user(
            email='owner@example.com', password='x',
            first_name='Owner', last_name='User', organization=self.org, role=User.Role.SUPERVISOR,
        )
        self.staff = User.objects.create_user(
            email='staff@example.com', password='x',
            first_name='Staff', last_name='User', organization=self.org, role=User.Role.STAFF,
        )
        self.other_supervisor = User.objects.create_user(
            email='supervisor2@example.com', password='x',
            first_name='Supervisor', last_name='Two', organization=self.org, role=User.Role.SUPERVISOR,
        )

    def test_private_view_visible_only_to_owner(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            SavedTableView.objects.create(
                table_key='client_programs', name='My View', created_by=self.owner,
                visibility=SavedTableView.Visibility.PRIVATE,
            )
            self.assertEqual(_visible_saved_views_qs(_request_for(self.owner), 'client_programs').count(), 1)
            self.assertEqual(_visible_saved_views_qs(_request_for(self.staff), 'client_programs').count(), 0)

    def test_everyone_view_visible_to_all_roles(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            SavedTableView.objects.create(
                table_key='client_programs', name='Team View', created_by=self.owner,
                visibility=SavedTableView.Visibility.EVERYONE,
            )
            self.assertEqual(_visible_saved_views_qs(_request_for(self.staff), 'client_programs').count(), 1)
            self.assertEqual(_visible_saved_views_qs(_request_for(self.other_supervisor), 'client_programs').count(), 1)

    def test_role_scoped_view_visible_only_to_named_roles(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            SavedTableView.objects.create(
                table_key='client_programs', name='Supervisors Only', created_by=self.owner,
                visibility=SavedTableView.Visibility.ROLES, roles=[User.Role.SUPERVISOR],
            )
            self.assertEqual(_visible_saved_views_qs(_request_for(self.staff), 'client_programs').count(), 0)
            self.assertEqual(_visible_saved_views_qs(_request_for(self.other_supervisor), 'client_programs').count(), 1)
            # The owner sees their own row regardless of role targeting.
            self.assertEqual(_visible_saved_views_qs(_request_for(self.owner), 'client_programs').count(), 1)

    def test_table_key_isolation(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            SavedTableView.objects.create(
                table_key='org_programs', name='Library View', created_by=self.owner,
                visibility=SavedTableView.Visibility.EVERYONE,
            )
            self.assertEqual(_visible_saved_views_qs(_request_for(self.staff), 'client_programs').count(), 0)
            self.assertEqual(_visible_saved_views_qs(_request_for(self.staff), 'org_programs').count(), 1)
