"""Login scheduling regressions using synthetic facilities and no services."""
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from apps.accounts.api import _queue_login_appointment_sync
from apps.accounts.models import User
from apps.integrations.tasks import queue_login_appointment_sync
from apps.tenants.models import Organization


class LoginAppointmentSyncTests(SimpleTestCase):
    def test_every_role_queues_own_facility_without_employee_or_client_link(self):
        for role in User.Role.values:
            with self.subTest(role=role):
                user = SimpleNamespace(pk=1, role=role, external_admin_id=702,
                                       external_employee_id=None, external_client_id=None)
                with mock.patch('apps.integrations.tasks.queue_login_appointment_sync') as queue:
                    _queue_login_appointment_sync(user, 9)
                queue.assert_called_once_with(9, 702)

    def test_users_without_facility_do_not_queue(self):
        for role in User.Role.values:
            with self.subTest(role=role):
                user = SimpleNamespace(pk=1, role=role, external_admin_id=None)
                with mock.patch('apps.integrations.tasks.queue_login_appointment_sync') as queue:
                    _queue_login_appointment_sync(user, 9)
                queue.assert_not_called()

    def _queue(self, *, checkpoint=None, active=None, connected=True):
        facility = SimpleNamespace(last_appointment_sync_at=checkpoint) if connected else None
        with (
            mock.patch('apps.integrations.tasks.transaction.atomic', return_value=nullcontext()),
            mock.patch('apps.integrations.tasks.transaction.on_commit') as on_commit,
            mock.patch('apps.integrations.tasks.OrganizationTpmsAdminId.objects.select_for_update') as facilities,
            mock.patch('apps.integrations.tasks.PullJob.objects.filter') as jobs,
            mock.patch('apps.integrations.tasks.PullJob.objects.create') as create,
            mock.patch('apps.integrations.tasks.run_pull_job.delay') as dispatch,
        ):
            facilities.return_value.filter.return_value.first.return_value = facility
            jobs.return_value.first.return_value = active
            job = queue_login_appointment_sync(9, 702)
            facilities.return_value.filter.assert_called_once_with(
                organization_id=9, admin_id=702,
                integration_platform=Organization.IntegrationPlatform.THERAPY_PMS,
            )
            if on_commit.called:
                dispatch.assert_not_called()
                on_commit.call_args.args[0]()
                dispatch.assert_called_once_with(job_id=job.id, org_id=9)
            return job, create

    def test_first_login_bootstraps_history_and_upcoming_appointments(self):
        job, create = self._queue()
        self.assertIs(job, create.return_value)
        params = create.call_args.kwargs['params']
        self.assertEqual(params['facility_admin_id'], 702)
        self.assertIsNone(params['updated_after'])
        self.assertEqual(params['to_date'], (timezone.localdate() + timedelta(days=90)).isoformat())
        self.assertTrue(params['auto_sync'])

    def test_later_login_preserves_incremental_checkpoint(self):
        checkpoint = timezone.now() - timedelta(days=1)
        _, create = self._queue(checkpoint=checkpoint)
        self.assertEqual(create.call_args.kwargs['params']['updated_after'], checkpoint.isoformat())

    def test_existing_job_is_reused_without_duplicate_dispatch(self):
        active = mock.Mock()
        job, create = self._queue(active=active)
        self.assertIs(job, active)
        create.assert_not_called()

    def test_disconnected_or_unmapped_facility_is_rejected(self):
        job, create = self._queue(connected=False)
        self.assertIsNone(job)
        create.assert_not_called()
