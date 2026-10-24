import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from ninja.errors import HttpError

from apps.accounts.security_audit import audit_security, record_security_event
from shared.audit import AuditJSONFormatter, log_event


class SecurityAuditTests(SimpleTestCase):
    def request(self):
        return SimpleNamespace(tenant=SimpleNamespace(pk=12), user=SimpleNamespace(pk=7),
            META={'REMOTE_ADDR': '127.0.0.1', 'HTTP_AUTHORIZATION': 'Bearer secret-token',
                  'HTTP_X_FORWARDED_FOR': 'attacker-input'}, request_id='12345678-1234-1234-1234-123456789abc',
            body=b'password=secret-password&pin=1234', GET={'token': 'query-secret'})

    def test_rejected_and_successful_calls_do_not_capture_inputs_or_results(self):
        @audit_security('pin.remove')
        def remove(request, data):
            raise HttpError(403, 'secret-password')
        with mock.patch('apps.accounts.security_audit.record_security_event') as record:
            with self.assertRaises(HttpError):
                remove(self.request(), {'password': 'secret-password'})
            record.assert_called_once_with('pin.remove', request=mock.ANY, actor_id=7, outcome='rejected', status=403)

    def test_mfa_challenge_is_not_a_successful_sign_in(self):
        @audit_security('auth.login')
        def login(request):
            return SimpleNamespace(mfa_required=True, mfa_token='secret-token')
        with mock.patch('apps.accounts.security_audit.record_security_event') as record:
            login(self.request())
            self.assertEqual(record.call_args.kwargs['outcome'], 'challenge')

    def test_formatter_preserves_structured_event_fields(self):
        with self.assertLogs('dcm.audit', level='INFO') as logs:
            log_event('pin.remove', 7, 'AccountSecurity', 7, {'outcome': 'success'})
        data = json.loads(AuditJSONFormatter().format(logs.records[0]))
        self.assertEqual(data['event_type'], 'pin.remove')
        self.assertEqual(data['actor_id'], 7)
        self.assertEqual(data['metadata'], {'outcome': 'success'})

    def test_safe_metadata_is_saved_in_both_sinks(self):
        with mock.patch('apps.accounts.security_audit.log_event') as structured, \
             mock.patch('apps.accounts.security_audit.transaction.atomic'), \
             mock.patch('apps.audit.models.AuditLog.objects.create') as create:
            record_security_event('pin.remove', request=self.request(), actor_id=7)
        values = create.call_args.kwargs
        self.assertEqual(values['organization_id'], 12)
        self.assertEqual(values['changes']['ip_address'], '127.0.0.1')
        self.assertEqual(values['changes']['event'], 'pin.remove')
        self.assertEqual(structured.call_args.args[-1], values['changes'])
        serialized = json.dumps(values)
        for secret in ('secret-password', 'secret-token', '1234"', 'attacker-input', 'query-secret'):
            self.assertNotIn(secret, serialized)

    def test_unknown_tenant_does_not_write_to_a_default_tenant(self):
        with mock.patch('apps.accounts.security_audit.log_event') as structured, \
             mock.patch('apps.audit.models.AuditLog.objects.create') as create:
            record_security_event('auth.login', outcome='rejected', status=401)
        create.assert_not_called()
        self.assertIsNone(structured.call_args.args[1])

    def test_database_sink_outage_keeps_structured_event_and_auth_result(self):
        with mock.patch('apps.accounts.security_audit.log_event') as structured, \
             mock.patch('apps.accounts.security_audit.transaction.atomic'), \
             mock.patch('apps.audit.models.AuditLog.objects.create', side_effect=RuntimeError('secret-password')), \
             self.assertLogs('apps.accounts.security_audit', level='ERROR') as errors:
            record_security_event('pin.remove', request=self.request(), actor_id=7)
        structured.assert_called_once()
        self.assertNotIn('secret-password', str(errors.output))

    def test_mfa_endpoint_only_records_success_after_code_check(self):
        from apps.accounts import api
        from apps.accounts.schemas import MfaTokenCodeRequest
        request = self.request()
        user = SimpleNamespace(pk=7)
        data = MfaTokenCodeRequest(mfa_token='secret-token', code='123456')
        with mock.patch.object(api.mfa, 'user_from_mfa_token', return_value=(user, 12)), \
             mock.patch.object(api.mfa, 'check_code', side_effect=HttpError(400, 'Incorrect code')), \
             mock.patch.object(api, '_issue_tokens') as issue, \
             mock.patch('apps.accounts.security_audit.record_security_event') as record:
            with self.assertRaises(HttpError):
                api.mfa_verify(request, data)
            issue.assert_not_called()
            self.assertEqual(record.call_args.kwargs['outcome'], 'rejected')
        with mock.patch.object(api.mfa, 'user_from_mfa_token', return_value=(user, 12)), \
             mock.patch.object(api.mfa, 'check_code'), \
             mock.patch.object(api, '_issue_tokens', return_value=SimpleNamespace(user_id=7)), \
             mock.patch('apps.accounts.security_audit.record_security_event') as record:
            api.mfa_verify(request, data)
            self.assertEqual(record.call_args.kwargs['outcome'], 'success')

    def test_request_tenant_takes_priority_and_invalid_metadata_is_discarded(self):
        request = self.request()
        request.META['REMOTE_ADDR'] = 'invalid-ip'
        request.request_id = 'untrusted-request-id'
        with mock.patch('apps.accounts.security_audit.log_event'), \
             mock.patch('apps.accounts.security_audit.transaction.atomic'), \
             mock.patch('apps.audit.models.AuditLog.objects.create') as create:
            record_security_event('pin.lockout', request=request, organization_id=99)
        values = create.call_args.kwargs
        self.assertEqual(values['organization_id'], 12)
        self.assertIsNone(values['ip_address'])
        self.assertEqual(values['request_id'], '')
        self.assertEqual(values['actor_id'], 0)

    def test_sso_redirect_rejection_is_not_recorded_as_success(self):
        from apps.accounts import api
        request = self.request()
        request.tenant = None
        with mock.patch('apps.accounts.security_audit.record_security_event') as record:
            response = api.sso_login(request, token='secret-token')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(record.call_args.kwargs['outcome'], 'rejected')
