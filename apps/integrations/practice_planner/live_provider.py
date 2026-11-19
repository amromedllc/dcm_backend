"""Live Wiley PracticePlanners adapter (Stage 6) — intentionally partial.

Two gaps are deliberate, not bugs, pending answers from Wiley:

1. Diagnosis lookup: Wiley has no code-based diagnosis search, only
   full-text search over problem descriptions (``/search?q=``). We
   approximate a diagnosis-code lookup by searching on the diagnosis's own
   code/display text. This is lossy, not an exact match.
2. Goal -> objective pairing: Wiley's ``goals`` array has no field linking
   a goal to a specific objective (``objectives`` is a separate flat array
   with its own nested ``interventions``). Our data model requires every
   objective to belong to exactly one goal. Inventing a pairing (e.g. by
   array position) risks misattributing clinical content, so goal,
   objective and intervention traversal raise ``ContentNotFound`` here
   until that's resolved with Wiley — ``get_component``/``children`` only
   serve problem-level content, plus each problem's progress notes and
   homework (which Wiley keys by problem number alone, independent of the
   goal/objective structure, so they're unaffected by the gap above).

Only wired behind ``PRACTICE_PLANNER_PROVIDER=live`` in service.py. The
separate ``is_mock``-only gate in access.py/planner_api.py still blocks any
real request from reaching this class — flipping that is a distinct,
deliberate rollout decision, not a side effect of this file existing.
"""
from .contracts import Category, Content, ContentNotFound, ContentPage, DiagnosisQuery, PlannerUnavailable
from .wiley_client import WileyApiError, fetch_json, fetch_pdf


def _reraise(exc: WileyApiError):
    """Every WileyApiError must become a PlannerError before leaving this module —
    contracts.safe_errors only knows how to handle PlannerUnavailable/ContentNotFound,
    not our HTTP client's own exception type, which would otherwise surface as a raw 500."""
    if exc.status_code == 403:
        raise PlannerUnavailable('Wiley API credentials were rejected') from exc
    if exc.status_code == 429:
        raise PlannerUnavailable('Wiley API rate limit reached; try again shortly') from exc
    raise PlannerUnavailable('Wiley API is currently unavailable') from exc

# Exact-prefix matches only, confirmed from Wiley's QA OpenAPI spec
# (2026-11-18). Deliberately excludes ambiguous pairs (e.g. FamilyTherapy2e
# vs Family2eProgressNotes/Family2eHomework, GroupTherapy2e vs
# GroupHomework) — confirm the real family boundary with Wiley rather than
# guess which planner a progress-note/homework database actually belongs to.
TP_FAMILY_MAP: dict[str, dict[str, str]] = {
    'Addiction_6e_TP': {'progress_note': 'Addiction_6e_PNP', 'homework': 'Addiction_6e_HW'},
    'Adolescent_6e_TP': {'progress_note': 'Adolescent_6e_PNP', 'homework': 'Adolescent_6e_HW'},
    'Adult_6e_TP': {'progress_note': 'Adult_6e_PNP', 'homework': 'Adult_6e_HW'},
    'Child_6e_TP': {'progress_note': 'Child_6e_PNP', 'homework': 'Child_6e_HW'},
    'Couples2e': {'progress_note': 'Couples2eProgressNotes', 'homework': 'Couples2eHomework'},
    'SevereAndPersistentMentalIllness2e': {'progress_note': 'SevereAndPersistentMentalIllness2eProgressNotes'},
    'Employee': {'homework': 'EmployeeHomework'},
    'ParentingSkills': {'homework': 'ParentingSkillsHomework'},
    'SchoolCounseling2e': {'homework': 'SchoolCounseling2eHomework'},
}

TP_DBS = frozenset((
    'Addiction_6e_TP', 'Adolescent_6e_TP', 'Adult_6e_TP', 'BehavioralMed', 'Child_6e_TP', 'CollegeCounseling',
    'CompleteWomens', 'ContinuumOfCare', 'CooccurringDisorders', 'Couples2e', 'CrisisCounselingTraumaticEvents2e',
    'Develomental_Therapy', 'EarlyChildhoodEducation', 'Employee', 'FamilyTherapy2e', 'GayLesbian',
    'GroupTherapy2e', 'JuvenileJustice', 'OlderAdult2e', 'ParentingSkills', 'PastoralPlanner_New',
    'PersonalityDisorders2e', 'ProbationParole', 'Psychopharmacology', 'Rehabilitation', 'SchoolCounseling2e',
    'SevereAndPersistentMentalIllness2e', 'SexualAbuse', 'SocialWork', 'SpecialEducationPlanner',
))

API_VERSION = 'v1'  # Wiley's API version, not a content edition — see contracts.Content.version.


def _attribution(db: str) -> str:
    return (f'Copyright © John Wiley & Sons, Inc. — {db} (Wiley PracticePlanners®). '
            'Used under license; confirm current attribution and retention terms before clinical use.')


def _parse_component_id(component_id: str) -> tuple[str, int]:
    parts = component_id.split(':')
    if len(parts) == 2 and parts[0] == 'problem':
        try:
            return parts[0], int(parts[1])
        except ValueError:
            pass
    raise ContentNotFound('Unknown or unsupported component reference')


class WileyLiveProvider:
    provider_id = 'wiley'

    def _problem_payload(self, db: str, problem_number: int) -> dict:
        try:
            return fetch_json(f'/v1/treatment_planners/problem/{db}/{problem_number}')
        except WileyApiError as exc:
            if exc.status_code in (400, 404):
                raise ContentNotFound('Problem not found') from exc
            _reraise(exc)

    def _problem_content(self, db: str, problem_number: int, payload: dict) -> Content:
        description = payload.get('problem', {}).get('Description', '')
        return Content(self.provider_id, db, f'problem:{problem_number}', Category.PROBLEM, description,
                        db, API_VERSION, 'en', _attribution(db), (), False)

    def lookup_diagnosis(self, query: DiagnosisQuery, *, edition, language='en', cursor=None):
        if language != 'en':
            raise ContentNotFound('Only English content is available from the live provider')
        if edition not in TP_DBS:
            raise ContentNotFound('Unknown treatment planner database')
        if cursor is not None:
            raise ContentNotFound('The live provider does not support pagination')
        search_term = (query.code or query.version)[:100]
        if len(search_term) < 3:
            raise ContentNotFound('Diagnosis search text is too short to query the live provider')
        try:
            data = fetch_json('/v1/treatment_planners/search', params={'q': search_term, 'limit': 50}, bucket='search')
        except WileyApiError as exc:
            if exc.status_code == 400:
                raise ContentNotFound('Invalid search text') from exc
            _reraise(exc)
        items = tuple(
            Content(self.provider_id, edition, f"problem:{row['problem_number']}", Category.PROBLEM, row['description'],
                     edition, API_VERSION, 'en', _attribution(edition), (), False)
            for row in data.get('results', []) if row.get('db') == edition
        )
        return ContentPage(items, None)

    def get_component(self, component_id, *, edition, language='en'):
        if language != 'en':
            raise ContentNotFound('Only English content is available from the live provider')
        kind, problem_number = _parse_component_id(component_id)
        payload = self._problem_payload(edition, problem_number)
        return self._problem_content(edition, problem_number, payload)

    def children(self, component_id, *, edition, language='en', cursor=None):
        if language != 'en':
            raise ContentNotFound('Only English content is available from the live provider')
        if cursor is not None:
            raise ContentNotFound('The live provider does not support pagination')
        kind, problem_number = _parse_component_id(component_id)
        self._problem_payload(edition, problem_number)  # validates the problem exists
        mapping = TP_FAMILY_MAP.get(edition, {})
        items: list[Content] = []
        if 'progress_note' in mapping:
            items.extend(self._progress_notes(edition, mapping['progress_note'], problem_number))
        if 'homework' in mapping:
            items.extend(self._homework(edition, mapping['homework'], problem_number))
        # Goals/objectives/interventions intentionally omitted — see module docstring.
        return ContentPage(tuple(items), None)

    def _progress_notes(self, edition: str, pnp_db: str, problem_number: int) -> list[Content]:
        try:
            data = fetch_json(f'/v1/treatment_planners/progress_note/{pnp_db}/{problem_number}')
        except WileyApiError as exc:
            if exc.status_code in (400, 404):
                return []
            _reraise(exc)
        parent = (f'problem:{problem_number}',)
        results = []
        for group in data.get('presentation_groups', []):
            for index, note in enumerate(group.get('notes', [])):
                results.append(Content(self.provider_id, edition, f"progress_note:{problem_number}:p{group['group_number']}:{index}",
                    Category.PROGRESS_NOTE, note, edition, API_VERSION, 'en', _attribution(edition), parent, False))
        for group in data.get('interventions', []):
            for index, note in enumerate(group.get('notes', [])):
                results.append(Content(self.provider_id, edition, f"progress_note:{problem_number}:i{group['intervention_number']}:{index}",
                    Category.PROGRESS_NOTE, note, edition, API_VERSION, 'en', _attribution(edition), parent, False))
        return results

    def _homework(self, edition: str, hw_db: str, problem_number: int) -> list[Content]:
        try:
            data = fetch_json(f'/v1/treatment_planners/homework/{hw_db}/{problem_number}')
        except WileyApiError as exc:
            if exc.status_code in (400, 404):
                return []
            _reraise(exc)
        parent = (f'problem:{problem_number}',)
        results = []
        for item in data.get('homework', []):
            text = item['title']
            if item.get('suggestions'):
                text = f"{text} — {item['suggestions']}"
            results.append(Content(self.provider_id, edition, f"homework:{problem_number}:{item['homework_number']}",
                Category.HOMEWORK, text, edition, API_VERSION, 'en', _attribution(edition), parent, False))
        return results


def fetch_homework_pdf(hw_db: str, homework_number: int) -> bytes:
    """Not part of the PracticePlannerProvider protocol (binary, not Content) —
    call directly from a dedicated endpoint when one is needed."""
    try:
        return fetch_pdf(f'/v1/treatment_planners/homework/{hw_db}/file/{homework_number}')
    except WileyApiError as exc:
        if exc.status_code == 404:
            raise ContentNotFound('No PDF is available for this homework item') from exc
        _reraise(exc)
