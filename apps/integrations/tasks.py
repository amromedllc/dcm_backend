"""
Celery tasks for TherapyPMS pulls (clients, providers, appointments).

Dispatched from apps.tenants.api instead of running inline in the request —
an appointments backfill spanning years used to be one blocking HTTP call
that could exceed both TPMS's own response time and the web server's
request timeout. Progress is tracked on the PullJob row so the admin UI can
poll it instead.
"""
import logging
from datetime import date

from celery import shared_task
from django.utils import timezone

from apps.integrations import tpms_pull
from apps.integrations.models import PullJob
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

logger = logging.getLogger(__name__)


def _mark_running(job: PullJob) -> None:
    job.status = PullJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=['status', 'started_at'])


def _mark_completed(job: PullJob, result: tpms_pull.PullResult) -> None:
    job.status = PullJob.Status.COMPLETED
    job.created_count = result.created
    job.updated_count = result.updated
    job.skipped_count = result.skipped
    job.error_message = '\n'.join(result.errors[:20])
    job.progress_current = job.progress_total
    job.finished_at = timezone.now()
    job.save(update_fields=[
        'status', 'created_count', 'updated_count', 'skipped_count',
        'error_message', 'progress_current', 'finished_at',
    ])


def _mark_failed(job: PullJob, error: str) -> None:
    job.status = PullJob.Status.FAILED
    job.error_message = error
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'error_message', 'finished_at'])


@shared_task(bind=True)
def run_pull_job(self, job_id: int, org_id: int) -> None:
    """Runs a queued PullJob. org_id must be passed as a keyword argument so
    config.celery's task_prerun signal can read it and switch the DB schema
    before this body runs; tenant_context(org_id) below additionally
    activates the row-level organization scope every TenantAwareModel query
    here needs (PullJob, Client, Appointment, Provider)."""
    org = Organization.objects.get(id=org_id)
    with tenant_context(org_id):
        job = PullJob.objects.get(id=job_id)
        _mark_running(job)
        try:
            if job.job_type == PullJob.JobType.CLIENTS:
                result = tpms_pull.pull_clients(org)
            elif job.job_type == PullJob.JobType.PROVIDERS:
                result = tpms_pull.pull_providers(org)
            elif job.job_type == PullJob.JobType.APPOINTMENTS:
                from_date = date.fromisoformat(job.params['from_date'])
                to_date = date.fromisoformat(job.params['to_date'])
                windows = tpms_pull._month_windows(from_date, to_date)
                job.progress_total = len(windows)
                job.save(update_fields=['progress_total'])

                def on_progress(done: int, total: int) -> None:
                    job.progress_current = done
                    job.save(update_fields=['progress_current'])

                result = tpms_pull.pull_appointments(
                    org,
                    from_date=from_date,
                    to_date=to_date,
                    patient_ids=job.params.get('patient_ids'),
                    staff_ids=job.params.get('staff_ids'),
                    on_progress=on_progress,
                )
            else:
                raise ValueError(f'Unknown pull job type: {job.job_type}')
            _mark_completed(job, result)
        except Exception as exc:
            logger.exception('Pull job %s (%s) failed', job_id, job.job_type)
            _mark_failed(job, str(exc))
