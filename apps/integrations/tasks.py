"""
Celery tasks for TherapyPMS pulls (clients, providers, appointments).

Dispatched from apps.tenants.api instead of running inline in the request —
an appointments backfill spanning years used to be one blocking HTTP call
that could exceed both TPMS's own response time and the web server's
request timeout. Progress is tracked on the PullJob row so the admin UI can
poll it instead.
"""
import logging
from datetime import date, timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.integrations import tpms_pull
from apps.integrations.models import PullJob
from apps.tenants.models import Organization, OrganizationTpmsAdminId
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
    job.cancelled_count = result.cancelled
    job.error_message = '\n'.join(result.errors[:20])
    job.progress_current = job.progress_total
    job.finished_at = timezone.now()
    job.save(update_fields=[
        'status', 'created_count', 'updated_count', 'skipped_count',
        'cancelled_count', 'error_message', 'progress_current', 'finished_at',
    ])


def _mark_failed(job: PullJob, error: str) -> None:
    job.status = PullJob.Status.FAILED
    job.error_message = error
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'error_message', 'finished_at'])


def queue_login_appointment_sync(org_id: int, facility_admin_id: int) -> PullJob | None:
    """Queue a deduplicated facility sync on login, including its initial pull."""
    with transaction.atomic():
        facility = OrganizationTpmsAdminId.objects.select_for_update().filter(
            organization_id=org_id,
            admin_id=facility_admin_id,
            integration_platform=Organization.IntegrationPlatform.THERAPY_PMS,
        ).first()
        if facility is None:
            logger.warning(
                'appointment_auto_sync not_queued org=%s facility=%s reason=facility_not_connected',
                org_id, facility_admin_id,
            )
            return None
        active_job = PullJob.objects.filter(
            job_type=PullJob.JobType.APPOINTMENTS,
            status__in=(PullJob.Status.PENDING, PullJob.Status.RUNNING),
            params__facility_admin_id=facility_admin_id,
        ).first()
        if active_job is not None:
            logger.info(
                'appointment_auto_sync existing_job org=%s facility=%s job=%s status=%s',
                org_id, facility_admin_id, active_job.pk, active_job.status,
            )
            return active_job

        today = timezone.localdate()
        try:
            from_date = today.replace(year=today.year - 1)
        except ValueError:
            from_date = today.replace(year=today.year - 1, day=28)
        job = PullJob.objects.create(
            job_type=PullJob.JobType.APPOINTMENTS,
            params={
                'facility_admin_id': facility_admin_id,
                'from_date': from_date.isoformat(),
                'to_date': (today + timedelta(days=90)).isoformat(),
                'updated_after': (
                    facility.last_appointment_sync_at.isoformat()
                    if facility.last_appointment_sync_at else None
                ),
                'auto_sync': True,
            },
        )
        logger.info(
            'appointment_auto_sync job_created org=%s facility=%s job=%s from=%s to=%s updated_after=%s',
            org_id, facility_admin_id, job.pk, from_date, job.params['to_date'],
            job.params['updated_after'],
        )

        def dispatch() -> None:
            try:
                run_pull_job.delay(job_id=job.id, org_id=org_id)
                logger.info(
                    'appointment_auto_sync dispatched org=%s facility=%s job=%s',
                    org_id, facility_admin_id, job.pk,
                )
            except Exception as exc:
                logger.exception('Failed to dispatch login appointment sync job %s', job.id)
                PullJob.objects.filter(pk=job.pk).update(
                    status=PullJob.Status.FAILED,
                    error_message=f'Failed to dispatch background task: {exc}',
                    finished_at=timezone.now(),
                )

        transaction.on_commit(dispatch)
        return job


LOGIN_ROSTER_SYNC_THROTTLE = timedelta(hours=24)


def _queue_login_roster_sync(
    org_id: int,
    facility_admin_id: int,
    *,
    job_type: str,
    last_synced_at_field: str,
) -> PullJob | None:
    """Queues a clients/providers pull on login, throttled to once per
    facility per LOGIN_ROSTER_SYNC_THROTTLE — unlike appointments (which
    change hour to hour and use updated_after for a cheap incremental
    pull), a roster pull re-fetches the whole facility list every time, so
    doing it on every login would be wasted TPMS load for data that rarely
    changes."""
    with transaction.atomic():
        facility = OrganizationTpmsAdminId.objects.select_for_update().filter(
            organization_id=org_id,
            admin_id=facility_admin_id,
            integration_platform=Organization.IntegrationPlatform.THERAPY_PMS,
        ).first()
        if facility is None:
            return None
        last_synced_at = getattr(facility, last_synced_at_field)
        if last_synced_at is not None and timezone.now() - last_synced_at < LOGIN_ROSTER_SYNC_THROTTLE:
            return None
        active_job = PullJob.objects.filter(
            job_type=job_type,
            status__in=(PullJob.Status.PENDING, PullJob.Status.RUNNING),
            params__facility_admin_id=facility_admin_id,
        ).first()
        if active_job is not None:
            return active_job

        job = PullJob.objects.create(
            job_type=job_type,
            params={'facility_admin_id': facility_admin_id, 'auto_sync': True},
        )
        logger.info(
            'roster_auto_sync job_created org=%s facility=%s job=%s type=%s',
            org_id, facility_admin_id, job.pk, job_type,
        )

        def dispatch() -> None:
            try:
                run_pull_job.delay(job_id=job.id, org_id=org_id)
            except Exception as exc:
                logger.exception('Failed to dispatch login %s sync job %s', job_type, job.id)
                PullJob.objects.filter(pk=job.pk).update(
                    status=PullJob.Status.FAILED,
                    error_message=f'Failed to dispatch background task: {exc}',
                    finished_at=timezone.now(),
                )

        transaction.on_commit(dispatch)
        return job


def queue_login_client_sync(org_id: int, facility_admin_id: int) -> PullJob | None:
    return _queue_login_roster_sync(
        org_id, facility_admin_id,
        job_type=PullJob.JobType.CLIENTS,
        last_synced_at_field='last_client_sync_at',
    )


def queue_login_provider_sync(org_id: int, facility_admin_id: int) -> PullJob | None:
    return _queue_login_roster_sync(
        org_id, facility_admin_id,
        job_type=PullJob.JobType.PROVIDERS,
        last_synced_at_field='last_provider_sync_at',
    )


@shared_task(bind=True)
def run_pull_job(self, job_id: int, org_id: int) -> None:
    """Runs a queued PullJob. Explicitly sets tenant connection and tenant_context
    so tenant-scoped models work safely across all Celery execution pools."""
    from django.db import connection

    org = Organization.objects.get(id=org_id)
    connection.set_tenant(org)
    with tenant_context(org_id):
        job = None
        try:
            job = PullJob.objects.get(id=job_id)
            if job.status != PullJob.Status.PENDING:
                return
            if timezone.now() - job.created_at > timedelta(minutes=3):
                _mark_failed(job, 'Job timed out before worker picked it up. Please try again.')
                return
            facility_admin_id = job.params.get('facility_admin_id')
            if facility_admin_id is None:
                _mark_failed(job, 'Please start a new sync for the selected facility.')
                return
            facility = org.tpms_admin_ids.get(admin_id=facility_admin_id)
            org = tpms_pull.FacilityConnection(org, facility)
            logger.info(
                'Starting pull job %s (%s) for org %s facility=%s auto_sync=%s',
                job_id, job.job_type, org_id, facility_admin_id,
                bool(job.params.get('auto_sync')),
            )
            _mark_running(job)
            if job.job_type == PullJob.JobType.CLIENTS:
                sync_started_at = timezone.now()
                result = tpms_pull.pull_clients(org)
                if facility.last_client_sync_at is None or facility.last_client_sync_at < sync_started_at:
                    facility.last_client_sync_at = sync_started_at
                    facility.save(update_fields=['last_client_sync_at'])
            elif job.job_type == PullJob.JobType.PROVIDERS:
                sync_started_at = timezone.now()
                result = tpms_pull.pull_providers(org)
                if facility.last_provider_sync_at is None or facility.last_provider_sync_at < sync_started_at:
                    facility.last_provider_sync_at = sync_started_at
                    facility.save(update_fields=['last_provider_sync_at'])
            elif job.job_type == PullJob.JobType.APPOINTMENTS:
                from_date = date.fromisoformat(job.params['from_date'])
                to_date = date.fromisoformat(job.params['to_date'])
                sync_started_at = timezone.now()
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
                    updated_after=job.params.get('updated_after'),
                    on_progress=on_progress,
                )
                if (
                    facility.last_appointment_sync_at is None
                    or facility.last_appointment_sync_at < sync_started_at
                ):
                    facility.last_appointment_sync_at = sync_started_at
                    facility.save(update_fields=['last_appointment_sync_at'])
            else:
                raise ValueError(f'Unknown pull job type: {job.job_type}')
            _mark_completed(job, result)
            logger.info(
                'Pull job %s (%s) completed for org %s facility=%s auto_sync=%s: created=%d, updated=%d, skipped=%d',
                job_id, job.job_type, org_id, facility_admin_id,
                bool(job.params.get('auto_sync')),
                result.created, result.updated, result.skipped,
            )
        except Exception as exc:
            logger.exception(
                'Pull job %s failed for org %s facility=%s auto_sync=%s',
                job_id, org_id,
                job.params.get('facility_admin_id') if job is not None else None,
                bool(job.params.get('auto_sync')) if job is not None else False,
            )
            if job is not None:
                _mark_failed(job, str(exc))
