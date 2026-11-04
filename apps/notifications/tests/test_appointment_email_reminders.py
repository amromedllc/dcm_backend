from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.core import mail
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError
from pydantic import ValidationError
from apps.accounts.models import User
from apps.clients.models import Client, ClientStaffAssignment
from apps.sessions.models import Appointment
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from apps.notifications.models import AppointmentReminderSettings, AppointmentEmailDelivery, NotificationPreference, RoleNotificationPolicy
from apps.notifications.appointment_reminders import send_organization_email_reminders, deliver, recipients_for, resolve_appointment_client
from apps.notifications.api import (AppointmentReminderSettingsInput, ClientAppointmentReminderInput,
    get_appointment_reminder_settings, update_appointment_reminder_settings,
    get_client_appointment_reminders, update_client_appointment_reminders)
from shared.tenancy import tenant_context, current_org_id


class AppointmentEmailReminderTests(TestCase):
    def setUp(self):
        self.org = Organization(name='Synthetic clinic', slug='reminders', schema_name='reminders')
        self.other = Organization(name='Other synthetic clinic', slug='other-reminders', schema_name='other_reminders')
        Organization.objects.bulk_create([self.org, self.other])
        self.context = tenant_context(self.org.pk)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=101, email_notifications_enabled=True)
        self.staff = User.objects.create_user('synthetic-staff@example.test', password='synthetic', role='staff', organization=self.org, external_admin_id=101, timezone='Asia/Kolkata')
        self.admin = User.objects.create_user('synthetic-admin@example.test', password='synthetic', role='admin', organization=self.org, external_admin_id=101)
        self.caregiver = User.objects.create_user('synthetic-caregiver@example.test', role='caregiver', organization=self.org, external_admin_id=101, external_client_id=501)
        self.client_row = Client.objects.create(first_name='Synthetic', last_name='Child', external_id='501', external_admin_id=101)
        ClientStaffAssignment.objects.create(client=self.client_row, user=self.staff)
        self.appt = Appointment.objects.create(external_client_id=501, external_admin_id=101, source='synced', staff=self.staff,
            start_time=timezone.now() + timedelta(minutes=20), end_time=timezone.now() + timedelta(minutes=80))
        self.config = AppointmentReminderSettings.objects.create(email_enabled=True, caregivers_enabled=True)
        self.request = SimpleNamespace(user=self.admin, tenant=self.org, _jwt_payload={'org_id': self.org.pk})

    def test_staff_and_linked_caregivers_receive_email_once(self):
        send_organization_email_reminders()
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(AppointmentEmailDelivery.objects.filter(status='sent').count(), 2)
        self.assertEqual({m.to[0] for m in mail.outbox}, {self.staff.email, self.caregiver.email})
        for message in mail.outbox:
            self.assertNotIn(self.client_row.full_name, message.body)
            self.assertNotIn('501', message.body)
            self.assertEqual(message.subject, 'Upcoming appointment')
            self.assertIn('Message-ID', message.extra_headers)
            self.assertTrue(message.alternatives)
        staff_message = next(m for m in mail.outbox if m.to == [self.staff.email])
        caregiver_message = next(m for m in mail.outbox if m.to == [self.caregiver.email])
        self.assertIn('IST', staff_message.body)
        self.assertIn('/portal', caregiver_message.alternatives[0][0])

    def test_defaults_preserve_staff_only_thirty_minute_reminders(self):
        self.config.delete()
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.staff.email])

    def test_caregiver_receives_reminder_without_staff_assignment(self):
        self.appt.staff = None
        self.appt.save()
        send_organization_email_reminders()
        self.assertEqual([m.to for m in mail.outbox], [[self.caregiver.email]])

    def test_native_assigned_staff_can_receive_reminder_for_a_practice_client(self):
        self.staff.external_admin_id = None
        self.staff.save()
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 2)

    def test_other_practice_assigned_staff_is_rejected(self):
        self.staff.external_admin_id = 202
        self.staff.save()
        send_organization_email_reminders()
        self.assertEqual([m.to for m in mail.outbox], [[self.caregiver.email]])

    def test_org_and_client_opt_out_are_hard_gates(self):
        self.config.email_enabled = False
        self.config.save()
        send_organization_email_reminders()
        self.assertEqual(AppointmentEmailDelivery.objects.count(), 0)
        self.config.email_enabled = True
        self.config.save()
        self.client_row.appointment_email_reminders_enabled = False
        self.client_row.save()
        send_organization_email_reminders()
        self.assertEqual(AppointmentEmailDelivery.objects.count(), 0)

    def test_client_cannot_override_disabled_org(self):
        self.config.email_enabled = False
        self.config.save()
        update_client_appointment_reminders(self.request, self.client_row.pk, ClientAppointmentReminderInput(email_enabled=True))
        self.assertFalse(get_client_appointment_reminders(self.request, self.client_row.pk)['effective_email_enabled'])

    def test_staff_and_parent_preferences_are_respected_independently(self):
        NotificationPreference.objects.create(recipient=self.staff, event_type='appointment_reminder', email_enabled=False)
        send_organization_email_reminders()
        self.assertEqual([m.to for m in mail.outbox], [[self.caregiver.email]])
        NotificationPreference.objects.create(recipient=self.caregiver, event_type='parent_session_reminder', email_enabled=False)
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_locked_role_policy_disables_email(self):
        RoleNotificationPolicy.objects.create(role='staff', event_type='appointment_reminder', email_enabled=False, locked=True)
        NotificationPreference.objects.create(recipient=self.staff, event_type='appointment_reminder', email_enabled=True)
        send_organization_email_reminders()
        self.assertEqual([m.to for m in mail.outbox], [[self.caregiver.email]])

    def test_practice_email_disable_suppresses_both_recipients(self):
        OrganizationTpmsAdminId.objects.filter(organization=self.org).update(email_notifications_enabled=False)
        send_organization_email_reminders()
        self.assertEqual(AppointmentEmailDelivery.objects.count(), 0)

    def test_cancelled_past_and_outside_window_appointments_are_skipped(self):
        self.appt.status = 'cancelled'
        self.appt.save()
        send_organization_email_reminders()
        self.appt.status = 'scheduled'
        self.appt.start_time = timezone.now() - timedelta(minutes=1)
        self.appt.save()
        send_organization_email_reminders()
        self.appt.start_time = timezone.now() + timedelta(hours=2)
        self.appt.save()
        send_organization_email_reminders()
        self.assertEqual(AppointmentEmailDelivery.objects.count(), 0)

    def test_configured_twenty_four_hour_window(self):
        self.appt.start_time = timezone.now() + timedelta(hours=23)
        self.appt.save()
        self.config.lead_minutes = 1440
        self.config.save()
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 2)

    def test_smtp_failure_retries_without_resending_successful_recipients(self):
        from apps.notifications.appointment_reminders import _send
        def fail_staff(delivery, appointment, recipient):
            if recipient.pk == self.staff.pk:
                raise OSError('Synthetic SMTP outage')
            return _send(delivery, appointment, recipient)
        with patch('apps.notifications.appointment_reminders._send', side_effect=fail_staff):
            send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 1)
        failed = AppointmentEmailDelivery.objects.get(recipient=self.staff)
        self.assertEqual(failed.status, 'failed')
        send_organization_email_reminders()  # Backoff has not elapsed.
        self.assertEqual(len(mail.outbox), 1)
        failed.retry_at = timezone.now() - timedelta(seconds=1)
        failed.save()
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 2)
        failed.refresh_from_db()
        self.assertEqual(failed.status, 'sent')
        self.assertEqual(failed.attempts, 2)

    def test_email_backend_not_accepting_message_records_failure(self):
        with patch('apps.notifications.appointment_reminders.EmailMultiAlternatives.send', return_value=0):
            deliver(self.appt.pk, self.staff.pk, self.appt.start_time)
        row = AppointmentEmailDelivery.objects.get(recipient=self.staff)
        self.assertEqual(row.status, 'failed')
        self.assertIsNone(row.sent_at)

    def test_inactive_caregiver_is_excluded(self):
        self.caregiver.is_active = False
        self.caregiver.save()
        send_organization_email_reminders()
        self.assertEqual([m.to for m in mail.outbox], [[self.staff.email]])

    def test_opt_out_and_cancellation_are_rechecked_before_retry(self):
        with patch('apps.notifications.appointment_reminders._send', side_effect=OSError('Synthetic outage')):
            send_organization_email_reminders()
        AppointmentEmailDelivery.objects.update(retry_at=timezone.now() - timedelta(seconds=1))
        self.client_row.appointment_email_reminders_enabled = False
        self.client_row.save()
        send_organization_email_reminders()
        self.appt.status = 'cancelled'
        self.appt.save()
        deliver(self.appt.pk, self.staff.pk, self.appt.start_time)
        self.assertEqual(AppointmentEmailDelivery.objects.filter(status='sent').count(), 0)

    def test_rescheduling_creates_a_new_occurrence(self):
        send_organization_email_reminders()
        self.appt.start_time += timedelta(minutes=5)
        self.appt.save()
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(AppointmentEmailDelivery.objects.count(), 4)

    def test_staff_reassignment_sends_only_to_new_assignee(self):
        self.config.caregivers_enabled = False
        self.config.save()
        send_organization_email_reminders()
        new_staff = User.objects.create_user('new-staff@example.test', organization=self.org, role='staff', external_admin_id=101)
        self.appt.staff = new_staff
        self.appt.save()
        send_organization_email_reminders()
        self.assertEqual([m.to for m in mail.outbox], [[self.staff.email], [new_staff.email]])

    def test_other_org_and_other_practice_caregivers_never_receive_email(self):
        User.objects.create_user('foreign-caregiver@example.test', role='caregiver', organization=self.other, external_admin_id=101, external_client_id=501)
        User.objects.create_user('other-practice@example.test', role='caregiver', organization=self.org, external_admin_id=202, external_client_id=501)
        send_organization_email_reminders()
        self.assertEqual(len(mail.outbox), 2)

    def test_foreign_staff_assignment_is_rejected(self):
        foreign = User.objects.create_user('foreign-staff@example.test', role='staff', organization=self.other, external_admin_id=101)
        Appointment.objects.filter(pk=self.appt.pk).update(staff_id=foreign.pk)
        send_organization_email_reminders()
        self.assertEqual([m.to for m in mail.outbox], [[self.caregiver.email]])

    def test_synced_identifier_collision_does_not_resolve_local_primary_key(self):
        collision = Client.objects.create(first_name='Wrong', last_name='Synthetic', external_id='999', external_admin_id=202)
        self.appt.external_client_id = collision.pk
        self.assertIsNone(resolve_appointment_client(self.appt))

    def test_native_client_uses_local_id(self):
        self.appt.source = 'manual'
        self.appt.external_client_id = self.client_row.pk
        self.assertEqual(resolve_appointment_client(self.appt), self.client_row)

    def test_ambiguous_external_client_is_skipped(self):
        Client.objects.create(first_name='Duplicate', last_name='Synthetic', external_id='501', external_admin_id=101)
        send_organization_email_reminders()
        self.assertEqual(AppointmentEmailDelivery.objects.count(), 0)

    def test_preference_failure_fails_closed(self):
        with patch('apps.notifications.appointment_reminders.NotificationPreference.objects.filter', side_effect=RuntimeError('Synthetic error')):
            send_organization_email_reminders()
        self.assertEqual(AppointmentEmailDelivery.objects.count(), 0)

    def test_active_claim_and_maximum_attempts_prevent_duplicate_send(self):
        row = AppointmentEmailDelivery.objects.create(appointment=self.appt, recipient=self.staff, scheduled_start=self.appt.start_time, status='sending', claimed_at=timezone.now(), attempts=1)
        deliver(self.appt.pk, self.staff.pk, self.appt.start_time)
        row.status, row.attempts = 'failed', 5
        row.save()
        deliver(self.appt.pk, self.staff.pk, self.appt.start_time)
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_claim_can_be_recovered(self):
        row = AppointmentEmailDelivery.objects.create(appointment=self.appt, recipient=self.staff, scheduled_start=self.appt.start_time, status='sending', claimed_at=timezone.now() - timedelta(minutes=11), attempts=1)
        deliver(self.appt.pk, self.staff.pk, self.appt.start_time)
        row.refresh_from_db()
        self.assertEqual(row.status, 'sent')
        self.assertEqual(row.attempts, 2)

    def test_staff_cannot_change_org_or_client_preferences(self):
        request = SimpleNamespace(user=self.staff, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        with self.assertRaises(HttpError):
            get_appointment_reminder_settings(request)
        with self.assertRaises(HttpError):
            update_client_appointment_reminders(request, self.client_row.pk, ClientAppointmentReminderInput(email_enabled=False))

    def test_cross_org_and_cross_practice_client_api_rejection(self):
        foreign = Client.objects.create(organization=self.other, first_name='Foreign', last_name='Synthetic', external_admin_id=101)
        other_practice = Client.objects.create(first_name='Practice', last_name='Synthetic', external_admin_id=202)
        for client in (foreign, other_practice):
            with self.assertRaises(HttpError):
                update_client_appointment_reminders(self.request, client.pk, ClientAppointmentReminderInput(email_enabled=False))

    def test_org_settings_and_client_inheritance(self):
        row = update_appointment_reminder_settings(self.request, AppointmentReminderSettingsInput(email_enabled=True, lead_minutes=1440, staff_enabled=True, caregivers_enabled=True))
        self.assertEqual(row.lead_minutes, 1440)
        update_client_appointment_reminders(self.request, self.client_row.pk, ClientAppointmentReminderInput(email_enabled=False))
        result = update_client_appointment_reminders(self.request, self.client_row.pk, ClientAppointmentReminderInput(email_enabled=None))
        self.assertIsNone(result['email_enabled'])
        self.assertTrue(result['effective_email_enabled'])
        with tenant_context(self.other.pk):
            self.assertEqual(AppointmentReminderSettings.objects.count(), 0)

    def test_timing_validation(self):
        for minutes in (0, 4, 10081):
            with self.assertRaises(ValidationError):
                AppointmentReminderSettingsInput(email_enabled=True, lead_minutes=minutes, staff_enabled=True, caregivers_enabled=False)

    def test_push_path_does_not_send_a_second_email(self):
        from apps.notifications.service import notify_appointment_reminder
        with patch('apps.notifications.service._send_firebase_push'), patch('apps.notifications.service._send_email') as send:
            notify_appointment_reminder(self.appt)
        send.assert_not_called()

    def test_scheduled_worker_sets_both_schema_and_row_contexts(self):
        from apps.notifications.tasks import send_upcoming_appointment_reminders
        Organization.objects.bulk_create([
            Organization(name='Public synthetic', slug='public', schema_name='public'),
            Organization(name='Inactive synthetic', slug='inactive', schema_name='inactive', is_active=False),
        ])
        visited = []
        @contextmanager
        def schema(name):
            visited.append(name)
            yield
        with patch('apps.notifications.tasks.schema_context', side_effect=schema), patch('apps.notifications.service.notify_appointment_reminder'), patch('apps.notifications.appointment_reminders.send_organization_email_reminders') as send:
            send.side_effect = lambda: self.assertIn(current_org_id(), (self.org.pk, self.other.pk))
            send_upcoming_appointment_reminders.run()
        self.assertEqual(set(visited), {self.org.schema_name, self.other.schema_name})
        self.assertEqual(send.call_count, 2)
