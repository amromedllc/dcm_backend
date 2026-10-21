from datetime import timedelta
from unittest import mock
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from apps.integrations.models import PullJob
from apps.integrations.tasks import run_pull_job
from apps.integrations.tpms_pull import FacilityConnection, PullResult


class PullJobRecoveryTests(SimpleTestCase):
    def test_worker_uses_the_facility_recorded_on_the_job(self):
        facility = SimpleNamespace(organization_id=1, admin_id=702, integration_email='south@example.com')
        org = SimpleNamespace(id=1, pk=1, tpms_admin_ids=mock.Mock())
        org.tpms_admin_ids.get.return_value = facility
        job = mock.Mock(status=PullJob.Status.PENDING, created_at=timezone.now(),
                        params={'facility_admin_id': 702}, job_type=PullJob.JobType.PROVIDERS)
        with (
            mock.patch('apps.integrations.tasks.Organization.objects.get', return_value=org),
            mock.patch('django.db.connection.set_tenant'),
            mock.patch('apps.integrations.tasks.PullJob.objects.get', return_value=job),
            mock.patch('apps.integrations.tasks.tpms_pull.pull_providers', return_value=PullResult()) as pull,
            mock.patch('apps.integrations.tasks._mark_completed'),
        ):
            run_pull_job.run(job_id=1, org_id=1)
        org.tpms_admin_ids.get.assert_called_once_with(admin_id=702)
        connection = pull.call_args.args[0]
        self.assertIsInstance(connection, FacilityConnection)
        self.assertEqual(connection.id, 1)
        self.assertEqual(connection.integration_email, 'south@example.com')

    def test_worker_does_not_replay_finished_or_expired_jobs(self):
        for status in (PullJob.Status.FAILED, PullJob.Status.COMPLETED, PullJob.Status.RUNNING):
            with self.subTest(status=status):
                job = mock.Mock(status=status)
                self._run(job)
                job.save.assert_not_called()

    def test_worker_expires_old_pending_job_without_calling_therapypms(self):
        job = mock.Mock(
            status=PullJob.Status.PENDING,
            created_at=timezone.now() - timedelta(minutes=4),
        )
        self._run(job)
        self.assertEqual(job.status, PullJob.Status.FAILED)
        self.assertIsNotNone(job.finished_at)
        job.save.assert_called_once()

    def test_worker_rejects_legacy_job_without_facility_selection(self):
        job = mock.Mock(status=PullJob.Status.PENDING, created_at=timezone.now(), params={})
        self._run(job)
        self.assertEqual(job.status, PullJob.Status.FAILED)
        self.assertIn('selected facility', job.error_message)

    def _run(self, job):
        with (
            mock.patch('apps.integrations.tasks.Organization.objects.get', return_value=mock.Mock()),
            mock.patch('django.db.connection.set_tenant'),
            mock.patch('apps.integrations.tasks.PullJob.objects.get', return_value=job),
            mock.patch('apps.integrations.tasks._mark_running') as start,
            mock.patch('apps.integrations.tasks.tpms_pull.pull_providers') as pull,
        ):
            run_pull_job.run(job_id=1, org_id=1)
            start.assert_not_called()
            pull.assert_not_called()
