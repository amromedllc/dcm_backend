from django.core import mail
from django.db import connection
from django.test import Client as DjangoClient, TestCase, override_settings
from django_tenants.utils import schema_context

from apps.accounts.auth import create_access_token
from apps.accounts.models import User
from apps.notifications.models import Notification, NotificationPreference, RoleNotificationPolicy
from apps.notifications.service import _create
from apps.tenants.models import Domain, Organization, OrganizationTpmsAdminId
from shared.tenancy import tenant_context


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class NotificationPreferenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org',
            slug='test-notifications',
            schema_name='test_notifications',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.user = User.objects.create_user(
            email='notify@example.com',
            password='x',
            first_name='Notify',
            last_name='User',
            organization=self.org,
        )
        self.admin = User.objects.create_user(
            email='admin-notify@example.com',
            password='x',
            first_name='Admin',
            last_name='User',
            organization=self.org,
            role=User.Role.ADMIN,
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

    def test_locked_role_policy_overrides_personal_preference_for_delivery(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            NotificationPreference.objects.create(
                recipient=self.user,
                event_type='report_review_request',
                email_enabled=True,
                web_enabled=True,
            )
            RoleNotificationPolicy.objects.create(
                role=User.Role.STAFF,
                event_type='report_review_request',
                email_enabled=False,
                web_enabled=False,
                locked=True,
            )

            _create(
                recipient_id=self.user.id,
                event_type='session_submitted',
                title='Session submitted for review',
                body='A session is ready.',
            )

            self.assertEqual(Notification.objects.count(), 0)
            self.assertEqual(len(mail.outbox), 0)

    def test_locked_role_policy_cannot_be_overridden_from_account_preferences(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            NotificationPreference.objects.create(
                recipient=self.user,
                event_type='target_mastered',
                email_enabled=False,
                web_enabled=False,
            )
            RoleNotificationPolicy.objects.create(
                role=User.Role.STAFF,
                event_type='target_mastered',
                email_enabled=True,
                web_enabled=True,
                locked=True,
            )

        token = create_access_token(self.user, self.org.pk)
        response = DjangoClient(
            HTTP_AUTHORIZATION=f'Bearer {token}',
            HTTP_HOST='localhost',
        ).put(
            '/api/v1/notifications/preferences',
            data={
                'preferences': [
                    {
                        'event_type': 'target_mastered',
                        'email_enabled': False,
                        'web_enabled': False,
                    },
                ],
            },
            content_type='application/json',
        )
        self.addCleanup(connection.set_schema_to_public)

        self.assertEqual(response.status_code, 200)
        target_row = next(
            row for row in response.json()
            if row['event_type'] == 'target_mastered'
        )
        self.assertTrue(target_row['locked'])
        self.assertTrue(target_row['email_enabled'])
        self.assertTrue(target_row['web_enabled'])

        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            pref = NotificationPreference.objects.get(
                recipient=self.user,
                event_type='target_mastered',
            )
            self.assertFalse(pref.email_enabled)
            self.assertFalse(pref.web_enabled)

    def test_unlocked_role_policy_returns_unlocked_account_preference(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            NotificationPreference.objects.create(
                recipient=self.user,
                event_type='target_mastered',
                email_enabled=False,
                web_enabled=False,
            )
            RoleNotificationPolicy.objects.create(
                role=User.Role.STAFF,
                event_type='target_mastered',
                email_enabled=True,
                web_enabled=True,
                locked=False,
            )

        token = create_access_token(self.user, self.org.pk)
        response = DjangoClient(
            HTTP_AUTHORIZATION=f'Bearer {token}',
            HTTP_HOST='localhost',
        ).get('/api/v1/notifications/preferences')
        self.addCleanup(connection.set_schema_to_public)

        self.assertEqual(response.status_code, 200)
        target_row = next(
            row for row in response.json()
            if row['event_type'] == 'target_mastered'
        )
        self.assertFalse(target_row['locked'])
        self.assertFalse(target_row['email_enabled'])
        self.assertFalse(target_row['web_enabled'])

    def test_admin_channel_policy_update_syncs_existing_account_preference(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            NotificationPreference.objects.create(
                recipient=self.user,
                event_type='target_mastered',
                email_enabled=True,
                web_enabled=True,
            )

        admin_token = create_access_token(self.admin, self.org.pk)
        save_response = DjangoClient(
            HTTP_AUTHORIZATION=f'Bearer {admin_token}',
            HTTP_HOST='localhost',
        ).put(
            '/api/v1/notifications/role-policies',
            data={
                User.Role.STAFF: {
                    'target_mastered': {
                        'email_enabled': False,
                        'web_enabled': False,
                        'locked': False,
                    },
                },
            },
            content_type='application/json',
        )
        self.addCleanup(connection.set_schema_to_public)

        self.assertEqual(save_response.status_code, 200)

        user_token = create_access_token(self.user, self.org.pk)
        account_response = DjangoClient(
            HTTP_AUTHORIZATION=f'Bearer {user_token}',
            HTTP_HOST='localhost',
        ).get('/api/v1/notifications/preferences')

        self.assertEqual(account_response.status_code, 200)
        target_row = next(
            row for row in account_response.json()
            if row['event_type'] == 'target_mastered'
        )
        self.assertFalse(target_row['locked'])
        self.assertFalse(target_row['email_enabled'])
        self.assertFalse(target_row['web_enabled'])

    def test_tpms_practice_gate_disables_email_delivery(self):
        OrganizationTpmsAdminId.objects.create(
            organization=self.org,
            admin_id=501,
            email_notifications_enabled=False,
        )
        self.user.external_admin_id = 501
        self.user.save(update_fields=['external_admin_id'])

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
            self.assertEqual(len(mail.outbox), 0)

    def test_tpms_practice_gate_allows_email_delivery_when_enabled(self):
        OrganizationTpmsAdminId.objects.create(
            organization=self.org,
            admin_id=501,
            email_notifications_enabled=True,
        )
        self.user.external_admin_id = 501
        self.user.save(update_fields=['external_admin_id'])

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
