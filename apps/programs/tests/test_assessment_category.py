"""Assessment is a program category: the API accepts it and rejects unknown ones."""
from django.test import SimpleTestCase
from pydantic import ValidationError

from apps.central_library.models import CentralProgram
from apps.programs.models import Program
from apps.programs.schemas import ProgramCreateRequest


class AssessmentCategoryTests(SimpleTestCase):
    def test_program_and_central_program_have_the_category(self):
        self.assertEqual(Program.Category.ASSESSMENT.value, 'assessment')
        self.assertIn('assessment', CentralProgram.Category.values)

    def test_create_request_accepts_assessment(self):
        request = ProgramCreateRequest(client_id=1, name='Skills check', category='assessment')
        self.assertEqual(request.category, Program.Category.ASSESSMENT)

    def test_unknown_category_is_still_rejected(self):
        with self.assertRaises(ValidationError):
            ProgramCreateRequest(client_id=1, name='Nope', category='not_a_category')
