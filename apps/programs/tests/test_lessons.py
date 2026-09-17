from types import SimpleNamespace

from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.clients.models import Client
from apps.programs.api import create_lesson, update_lesson
from apps.programs.models import Lesson, LessonProgram, Program
from apps.programs.schemas import LessonCreateRequest, LessonUpdateRequest
from apps.sessions.api import start_session
from apps.sessions.models import SessionPrototype
from apps.sessions.schemas import SessionStartRequest
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class LessonPlaylistTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Lessons Org', slug='lessons-org', schema_name='lessons_org',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.admin = User.objects.create_user(
            email='lessons-admin@example.com',
            password='x',
            first_name='Lessons',
            last_name='Admin',
            organization=self.org,
            role=User.Role.ADMIN,
            external_admin_id=801,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.client_row = Client.objects.create(
                first_name='Lessons',
                last_name='Client',
                external_id='9901',
                external_admin_id=801,
                organization=self.org,
            )
            self.other_client = Client.objects.create(
                first_name='Other',
                last_name='Client',
                external_id='9902',
                external_admin_id=801,
                organization=self.org,
            )
            self.program_a = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Matching',
                created_by=self.admin,
            )
            self.program_b = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Mand Training',
                created_by=self.admin,
            )
            self.other_program = Program.objects.create(
                external_client_id=int(self.other_client.external_id),
                name='Other Client Program',
                created_by=self.admin,
            )

    def _request(self):
        return SimpleNamespace(user=self.admin)

    def test_create_lesson_playlist_saves_message_and_program_order(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            status, payload = create_lesson(
                self._request(),
                LessonCreateRequest(
                    client_id=int(self.client_row.external_id),
                    name='Morning Session',
                    therapist_message='Start with pairing, then table work.',
                    program_ids=[self.program_b.id, self.program_a.id],
                ),
            )

            self.assertEqual(status, 201)
            self.assertEqual(payload['therapist_message'], 'Start with pairing, then table work.')
            self.assertEqual(
                [p['program_id'] for p in payload['programs']],
                [self.program_b.id, self.program_a.id],
            )
            self.assertEqual(
                list(LessonProgram.objects.filter(lesson_id=payload['id']).values_list('program_id', flat=True)),
                [self.program_b.id, self.program_a.id],
            )

    def test_update_lesson_playlist_replaces_ordered_programs(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            lesson = Lesson.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Initial',
                created_by=self.admin,
            )
            LessonProgram.objects.create(lesson=lesson, program=self.program_a, display_order=0)

            payload = update_lesson(
                self._request(),
                lesson.id,
                LessonUpdateRequest(
                    name='Updated Playlist',
                    therapist_message='Use errorless prompting.',
                    program_ids=[self.program_b.id, self.program_a.id],
                ),
            )

            self.assertEqual(payload['name'], 'Updated Playlist')
            self.assertEqual(payload['therapist_message'], 'Use errorless prompting.')
            self.assertEqual(
                [p['program_id'] for p in payload['programs']],
                [self.program_b.id, self.program_a.id],
            )

    def test_lesson_playlist_rejects_programs_from_another_client(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with self.assertRaises(HttpError):
                create_lesson(
                    self._request(),
                    LessonCreateRequest(
                        client_id=int(self.client_row.external_id),
                        name='Mixed Client Playlist',
                        program_ids=[self.program_a.id, self.other_program.id],
                    ),
                )

    def test_start_session_from_playlist_uses_playlist_name_and_message(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            SessionPrototype.objects.create(
                name='Default Prototype',
                message_to_therapist='Default message',
                is_default=True,
                created_by=self.admin,
            )
            lesson = Lesson.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Playlist Session',
                therapist_message='Playlist message',
                created_by=self.admin,
            )
            LessonProgram.objects.create(lesson=lesson, program=self.program_a, display_order=0)

            status, payload = start_session(
                self._request(),
                SessionStartRequest(
                    client_id=int(self.client_row.external_id),
                    lesson_id=lesson.id,
                ),
            )

            self.assertEqual(status, 201)
            self.assertEqual(payload['session_name'], 'Playlist Session')
            self.assertEqual(payload['message_to_therapist'], 'Playlist message')
