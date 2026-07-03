from ninja import NinjaAPI
from apps.accounts.api import router as accounts_router
from apps.accounts.auth import jwt_auth, api_key_auth
from apps.clients.api import router as clients_router
from apps.programs.api import router as programs_router
from apps.sessions.api import router as sessions_router
from apps.notes.api import router as notes_router
from apps.analytics.api import router as analytics_router
from apps.exports.api import router as exports_router
from apps.notifications.api import router as notifications_router

api = NinjaAPI(
    title='DCM Platform API',
    version='1.0.0',
    description=(
        'Data Collection Platform API. '
        'Authenticate with Bearer JWT (users) or X-API-Key header (facility integrations).'
    ),
    docs_url='/docs',
    auth=[jwt_auth, api_key_auth],
)

api.add_router('/auth', accounts_router, tags=['Authentication'])
api.add_router('/clients', clients_router, tags=['Clients'])
api.add_router('/', programs_router, tags=['Programs'])
api.add_router('/', sessions_router, tags=['Sessions'])
api.add_router('/', notes_router, tags=['Notes'])
api.add_router('/', analytics_router, tags=['Analytics'])
api.add_router('/', exports_router, tags=['Exports'])
api.add_router('/', notifications_router, tags=['Notifications'])

# Routers added in future stages:
# from apps.integrations.api import router as integrations_router  # Stage 8


@api.get('/health', auth=None, tags=['System'])
def health_check(request):
    return {'status': 'ok', 'version': '1.0.0'}
