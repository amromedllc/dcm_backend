from django.conf import settings
from django.core.exceptions import PermissionDenied

from .contracts import Category, PlannerUnavailable


class WileyPracticePlannerService:
    """Internal content-only facade. Do not retain across requests or expose raw providers."""

    def __init__(self, provider):
        self._provider = provider

    def lookup_diagnosis(self, query, **options):
        return self._provider.lookup_diagnosis(query, **options)

    def get_component(self, component_id, **options):
        return self._provider.get_component(component_id, **options)

    def children(self, component_id, **options):
        return self._provider.children(component_id, **options)

    def progress_notes(self, problem_id, **options):
        return self._related(problem_id, Category.PROGRESS_NOTE, **options)

    def homework(self, problem_id, **options):
        return self._related(problem_id, Category.HOMEWORK, **options)

    def _related(self, problem_id, category, **options):
        # Consume all pages: prompts must not disappear behind hierarchy pagination.
        result, cursor = [], None
        seen = set()
        while True:
            page = self.children(problem_id, cursor=cursor, **options)
            result.extend(c for c in page.items if c.category == category)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(result)
            if cursor in seen:
                raise PlannerUnavailable('Invalid provider pagination')
            seen.add(cursor)


def get_practice_planner_service():
    """Internal factory. Disabled by default; synthetic content only in DEBUG.

    The live branch exists for direct testing of the Stage 6 adapter only —
    reaching it through a real request still requires the separate
    `is_mock`-only gate in access.py/planner_api.py to be lifted, which is a
    distinct rollout decision, not a side effect of configuring this setting.
    """
    configured = getattr(settings, 'PRACTICE_PLANNER_PROVIDER', 'disabled')
    if configured == 'mock' and settings.DEBUG:
        from .mock import MockPracticePlannerProvider
        return WileyPracticePlannerService(MockPracticePlannerProvider())
    if configured == 'live' and getattr(settings, 'WILEY_API_KEY', ''):
        from .live_provider import WileyLiveProvider
        return WileyPracticePlannerService(WileyLiveProvider())
    raise PlannerUnavailable('Practice planner provider unavailable')


def service_for_user(request):
    """Recheck the authenticated request's facility gate on every operation.

    Do not cache a service across requests. Client-specific endpoints must also
    apply their existing permission/assignment checks before accessing a client.
    """
    from .access import availability_for_request
    if not availability_for_request(request)['enabled']:
        raise PermissionDenied('Practice planner access is not enabled')
    return get_practice_planner_service()
