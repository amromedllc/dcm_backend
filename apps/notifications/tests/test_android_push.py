from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from pydantic import ValidationError

from apps.notifications.api import FirebaseTokenUpsert
from apps.notifications.service import _send_firebase_push


class AndroidPushTests(SimpleTestCase):
    def test_registration_accepts_explicit_android_and_rejects_unknown_platforms(self):
        self.assertEqual(FirebaseTokenUpsert(token='device', platform='android').platform, 'android')
        self.assertIsNone(FirebaseTokenUpsert(token='old-browser').platform)
        with self.assertRaises(ValidationError):
            FirebaseTokenUpsert(token='device', platform='unknown')

    def test_browser_only_delivery_filters_out_android_tokens(self):
        tokens = mock.Mock()
        tokens.filter.return_value = []
        with mock.patch('apps.notifications.service._firebase_app', return_value=mock.Mock()), \
             mock.patch('apps.notifications.models.FirebaseMessagingToken.all_organizations.filter', return_value=tokens), \
             mock.patch('firebase_admin.messaging.send') as send:
            _send_firebase_push(SimpleNamespace(id=1), 'Title', 'Body', {}, platforms=['web'])
        tokens.filter.assert_called_once_with(platform__in=['web'])
        send.assert_not_called()

    def test_disabled_channels_do_not_initialize_or_send_to_firebase(self):
        with mock.patch('apps.notifications.service._channel_enabled', return_value=False), \
             mock.patch('apps.notifications.service._firebase_app') as firebase:
            _send_firebase_push(SimpleNamespace(id=1), 'Title', 'Body', {}, event_type='target_mastered')
        firebase.assert_not_called()
