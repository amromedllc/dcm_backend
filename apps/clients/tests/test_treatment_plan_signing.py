"""Treatment plan signing: who can sign, when, and what signing locks."""
from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.clients import api as clients_api
from apps.clients.models import Client, TreatmentPlan, TreatmentPlanSignature
from apps.clients.schemas import SignTreatmentPlanRequest, TreatmentPlanUpdateRequest
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class TreatmentPlanSigningTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Plan Org', slug='plan-org', schema_name='plan_org')
        self.supervisor = User.objects.create_user(
            email='plan-sup@example.com', password='x', first_name='Sam', last_name='Supervisor',
            organization=self.org, role=User.Role.SUPERVISOR,
        )
        self.staff = User.objects.create_user(
            email='plan-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        patcher = mock.patch.object(clients_api, '_get_client_or_404', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = Client.objects.create(first_name='Ari', last_name='K', external_id='9001', organization=self.org)
            self.client_id = client.id
            self.plan = TreatmentPlan.objects.create(
                client=client, title='Plan Q1', plan_date=date(2026, 9, 1),
                sections={'goals': ['Request items']}, status=TreatmentPlan.Status.FINALIZED,
            )

    def _ctx(self):
        return schema_context(self.org.schema_name), tenant_context(self.org.pk)

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org, META={'REMOTE_ADDR': '10.0.0.1'})

    def _sign(self, user=None, **overrides):
        payload = dict(signature_data='Sam Supervisor', attested=True)
        payload.update(overrides)
        return clients_api.sign_treatment_plan(
            self._request(user or self.supervisor), self.client_id, self.plan.id, SignTreatmentPlanRequest(**payload),
        )

    def test_supervisor_can_sign_a_finalized_plan(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            status, plan = self._sign()
            saved = TreatmentPlanSignature.objects.get()
        self.assertEqual(status, 201)
        self.assertTrue(plan['is_signed'])
        self.assertEqual(plan['signatures'][0]['signer_name'], 'Sam Supervisor')
        self.assertEqual(saved.signer_role, 'supervisor')
        self.assertEqual(len(saved.content_hash), 64)

    def test_staff_cannot_sign(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            with self.assertRaises(HttpError) as raised:
                self._sign(user=self.staff)
        self.assertEqual(raised.exception.status_code, 403)

    def test_only_finalized_plans_can_be_signed(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            TreatmentPlan.objects.filter(pk=self.plan.pk).update(status='draft')
            with self.assertRaises(HttpError) as raised:
                self._sign()
        self.assertEqual(raised.exception.status_code, 409)

    def test_attestation_and_name_are_required(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            for overrides in (dict(attested=False), dict(signature_data='   ')):
                with self.subTest(overrides):
                    with self.assertRaises(HttpError) as raised:
                        self._sign(**overrides)
                    self.assertEqual(raised.exception.status_code, 400)

    def test_cannot_sign_twice(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            self._sign()
            with self.assertRaises(HttpError) as raised:
                self._sign()
        self.assertEqual(raised.exception.status_code, 409)

    def test_signed_plan_is_locked_but_can_be_archived(self):
        ctx = self._ctx()
        with ctx[0], ctx[1]:
            self._sign()
            request = self._request(self.supervisor)
            for update in (
                TreatmentPlanUpdateRequest(title='Changed'),
                TreatmentPlanUpdateRequest(sections={'goals': []}),
                TreatmentPlanUpdateRequest(status='draft'),
            ):
                with self.subTest(update.dict(exclude_unset=True)):
                    with self.assertRaises(HttpError) as raised:
                        clients_api.update_treatment_plan(request, self.client_id, self.plan.id, update)
                    self.assertEqual(raised.exception.status_code, 409)
            with self.assertRaises(HttpError):
                clients_api.delete_treatment_plan(request, self.client_id, self.plan.id)
            archived = clients_api.update_treatment_plan(
                request, self.client_id, self.plan.id, TreatmentPlanUpdateRequest(status='archived'),
            )
        self.assertEqual(archived['status'], 'archived')
