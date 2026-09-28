from django.test import SimpleTestCase, override_settings

from shared import ai_client


class AiFlagTests(SimpleTestCase):
    @override_settings(AI_ENABLED=False, AI_API_KEY='k')
    def test_off_flag_disables_ai_even_with_key(self):
        self.assertFalse(ai_client.is_enabled())
        with self.assertRaises(ai_client.AIError) as ctx:
            ai_client.chat_json('s', 'u')
        self.assertEqual(ctx.exception.status, 403)

    @override_settings(AI_ENABLED=True, AI_API_KEY='')
    def test_on_flag_without_key_is_not_enabled(self):
        self.assertFalse(ai_client.is_enabled())

    @override_settings(AI_ENABLED=True, AI_API_KEY='k')
    def test_on_flag_with_key_is_enabled(self):
        self.assertTrue(ai_client.is_enabled())
