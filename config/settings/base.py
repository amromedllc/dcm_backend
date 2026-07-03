import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
APPEND_SLASH = False

# ---------------------------------------------------------------------------
# Multi-tenant setup (django-tenants, schema-based isolation)
# ---------------------------------------------------------------------------

SHARED_APPS = [
    'django_tenants',
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.admin',
    # Platform shared apps
    'apps.tenants',
    'apps.accounts',
    # Read-only mirror of the TherapyPMS source database
    'apps.legacy',
]

TENANT_APPS = [
    'django.contrib.contenttypes',
    # Clinical domain apps — each facility gets its own schema
    'apps.clients',
    'apps.programs',
    'apps.sessions',
    'apps.notes',
    'apps.analytics',
    'apps.exports',
    'apps.integrations',
    'apps.notifications',
]

INSTALLED_APPS = list(SHARED_APPS) + [
    app for app in TENANT_APPS if app not in SHARED_APPS
]

TENANT_MODEL = 'tenants.Organization'
TENANT_DOMAIN_MODEL = 'tenants.Domain'

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    'django_tenants.middleware.main.TenantMainMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database — schema-based multi-tenancy via django-tenants
# ---------------------------------------------------------------------------

DATABASES = {
    'default': {
        'ENGINE': 'django_tenants.postgresql_backend',
        'NAME': env('DB_NAME', default='dcm'),
        'USER': env('DB_USER', default='dcm_user'),
        'PASSWORD': env('DB_PASSWORD', default='dcm_pass'),
        'HOST': env('DB_HOST', default='localhost'),
        'PORT': env('DB_PORT', default='5432'),
        'CONN_MAX_AGE': 60,
    },
    # Read-only connection to the TherapyPMS source-of-truth database.
    # Django never migrates this DB — schema is owned by the Laravel app.
    'therapypms': {
        'ENGINE': 'apps.legacy.backend',
        'NAME': env('TPMS_DB_NAME', default='therapypms'),
        'USER': env('TPMS_DB_USER', default='tpms_readonly'),
        'PASSWORD': env('TPMS_DB_PASSWORD', default=''),
        'HOST': env('TPMS_DB_HOST', default='localhost'),
        'PORT': env('TPMS_DB_PORT', default='5432'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {
            'options': '-c default_transaction_read_only=on',
        },
    },
}

DATABASE_ROUTERS = [
    'apps.legacy.router.TherapyPmsRouter',
    'django_tenants.routers.TenantSyncRouter',
]

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

JWT_SECRET_KEY = env('JWT_SECRET_KEY', default=SECRET_KEY)
JWT_ALGORITHM = 'HS256'
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = env.int('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', default=15)
JWT_REFRESH_TOKEN_EXPIRE_DAYS = env.int('JWT_REFRESH_TOKEN_EXPIRE_DAYS', default=7)

# ---------------------------------------------------------------------------
# DocuSeal SSO
# ---------------------------------------------------------------------------

DOCUSEAL_BASE_URL = env('DOCUSEAL_BASE_URL')
# Matches SSO_JWT_SECRET in docuseal/app/controllers/sso_login_controller.rb —
# must stay identical on both sides or tokens minted here won't decode there.
DOCUSEAL_SSO_SECRET = env('DOCUSEAL_SSO_SECRET')

# ---------------------------------------------------------------------------
# Redis + Celery
# ---------------------------------------------------------------------------

REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes max per task

# ---------------------------------------------------------------------------
# Static + Media files
# ---------------------------------------------------------------------------

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Tenant base domain (used for subdomain routing)
# ---------------------------------------------------------------------------

TENANT_BASE_DOMAIN = env('TENANT_BASE_DOMAIN', default='localhost')

# ---------------------------------------------------------------------------
# CORS (configured per environment)
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS: list[str] = []

# ---------------------------------------------------------------------------
# Unfold admin theme
# ---------------------------------------------------------------------------

UNFOLD = {
    'SITE_TITLE': 'DCM Admin',
    'SITE_HEADER': 'DCM Platform',
    'SITE_SUBHEADER': 'Data Collection & Management',
    'SITE_URL': '/',
    'SITE_ICON': None,
    'SITE_SYMBOL': 'monitoring',
    'SHOW_HISTORY': True,
    'SHOW_VIEW_ON_SITE': True,
    'THEME': 'dark',
    'COLORS': {
        'primary': {
            '50':  '240 253 250',
            '100': '204 251 241',
            '200': '153 246 228',
            '300': '94 234 212',
            '400': '45 212 191',
            '500': '20 184 166',
            '600': '13 148 136',
            '700': '15 118 110',
            '800': '17 94 89',
            '900': '19 78 74',
            '950': '4 47 46',
        },
    },
    'SIDEBAR': {
        'show_search': True,
        'show_all_applications': True,
        'navigation': [
            {
                'title': 'Platform',
                'items': [
                    {
                        'title': 'Organizations',
                        'icon': 'business',
                        'link': '/admin/tenants/organization/',
                    },
                    {
                        'title': 'Users',
                        'icon': 'people',
                        'link': '/admin/accounts/user/',
                    },
                ],
            },
            {
                'title': 'Clinical',
                'items': [
                    {
                        'title': 'Programs',
                        'icon': 'menu_book',
                        'link': '/admin/programs/program/',
                    },
                    {
                        'title': 'Targets',
                        'icon': 'track_changes',
                        'link': '/admin/programs/target/',
                    },
                    {
                        'title': 'Sessions',
                        'icon': 'assignment',
                        'link': '/admin/dcm_sessions/sessionrun/',
                    },
                    {
                        'title': 'Appointments',
                        'icon': 'calendar_month',
                        'link': '/admin/dcm_sessions/appointment/',
                    },
                    {
                        'title': 'Notes',
                        'icon': 'note_alt',
                        'link': '/admin/notes/lessonnote/',
                    },
                    {
                        'title': 'Note Templates',
                        'icon': 'description',
                        'link': '/admin/notes/notetemplate/',
                    },
                    {
                        'title': 'Clients',
                        'icon': 'person',
                        'link': '/admin/clients/client/',
                    },
                    {
                        'title': 'Analytics',
                        'icon': 'bar_chart',
                        'link': '/admin/analytics/graphannotation/',
                    },
                ],
            },
        ],
    },
}
