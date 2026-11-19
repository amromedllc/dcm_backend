"""Original, nonclinical fixtures. No Wiley text, credentials or ICD crosswalks."""
from .contracts import Category, Content, ContentNotFound, ContentPage, DiagnosisQuery


class MockPracticePlannerProvider:
    provider_id = 'synthetic'
    page_size = 1

    def _catalog(self, edition, language):
        if edition not in ('test-1', 'test-2') or language != 'en':
            raise ContentNotFound('Synthetic edition or language not found')
        rows = (
            ('p1', Category.PROBLEM, 'Synthetic practice scenario A.', (), False),
            ('p2', Category.PROBLEM, 'Synthetic practice scenario B.', (), False),
            ('g1', Category.GOAL, 'Demonstrate the fictional practice routine.', ('p1', 'p2'), False),
            ('o1', Category.OBJECTIVE, 'Complete one fictional practice step.', ('g1',), False),
            ('i1', Category.INTERVENTION, 'Rehearse the fictional step with a sample cue.' if edition == 'test-1' else 'Rehearse the revised fictional step with two sample cues.', ('o1',), False),
            ('i2', Category.INTERVENTION, 'Use the legacy fictional cue.', ('o1',), edition == 'test-2'),
            ('n1', Category.PROGRESS_NOTE, 'Synthetic prompt: describe the fictional practice attempt.', ('p1',), False),
            ('h1', Category.HOMEWORK, 'Synthetic exercise: repeat the fictional routine with sample objects.', ('p1',), False),
        )
        return tuple(Content(
            self.provider_id, 'synthetic-planner', key, category,
            'TEST ONLY — NOT FOR CLINICAL USE. ' + text, edition, edition,
            language, 'Original Progressly synthetic fixture; no Wiley content.', parents, retired,
        ) for key, category, text, parents, retired in rows)

    def _page(self, items, cursor):
        try:
            offset = 0 if cursor is None else int(cursor)
        except (ValueError, TypeError):
            raise ContentNotFound('Invalid synthetic cursor') from None
        if offset < 0 or offset > len(items):
            raise ContentNotFound('Invalid synthetic cursor')
        end = offset + self.page_size
        return ContentPage(tuple(items[offset:end]), str(end) if end < len(items) else None)

    def lookup_diagnosis(self, query: DiagnosisQuery, *, edition, language='en', cursor=None):
        catalog = self._catalog(edition, language)
        # Fake codes only. Do not infer equivalence between ICD and DSM codes.
        match = (query.system, query.version, query.code) in {
            ('SYNTHETIC', 'test', 'SYNTHETIC-001'),
            ('ICD-10-CM', 'test', 'TEST-001'),
        }
        return self._page([c for c in catalog if c.category == Category.PROBLEM] if match else [], cursor)

    def get_component(self, component_id, *, edition, language='en'):
        for content in self._catalog(edition, language):
            if content.component_id == component_id:
                return content
        raise ContentNotFound('Synthetic component not found')

    def children(self, component_id, *, edition, language='en', cursor=None):
        self.get_component(component_id, edition=edition, language=language)
        return self._page([c for c in self._catalog(edition, language) if component_id in c.parent_ids], cursor)
