from django.core import mail
from django.test import TestCase, override_settings
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.service import _create
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class NotificationPreferenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org',
            slug='test-notifications',
            schema_name='test_notifications',
        )
        self.user = User.objects.create_user(
            email='notify@example.com',
            password='x',
            first_name='Notify',
            last_name='User',
            organization=self.org,
        )

    def test_web_and_email_preferences_disable_delivery(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            NotificationPreference.objects.create(
                recipient=self.user,
                event_type='report_review_request',
                email_enabled=False,
                web_enabled=False,
            )

            _create(
                recipient_id=self.user.id,
                event_type='session_submitted',
                title='Session submitted for review',
                body='A session is ready.',
            )

            self.assertEqual(Notification.objects.count(), 0)
            self.assertEqual(len(mail.outbox), 0)

    def test_enabled_preferences_create_web_notification_and_email(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            NotificationPreference.objects.create(
                recipient=self.user,
                event_type='target_mastered',
                email_enabled=True,
                web_enabled=True,
            )

            _create(
                recipient_id=self.user.id,
                event_type='target_advanced',
                title='Target advanced',
                body='A target advanced automatically.',
            )

            self.assertEqual(Notification.objects.count(), 1)
            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, ['notify@example.com'])
