import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', default=False)
APPEND_SLASH = False

if DEBUG:
    ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
else:
    # No default in production — fail loudly at startup rather than silently
    # accepting requests for an unconfigured Host header.
    ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')



CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=['https://api.progressly.io'])

# ---------------------------------------------------------------------------
# Multi-tenant setup (django-tenants, schema-based isolation)
# ---------------------------------------------------------------------------

SHARED_APPS = [
    'django_tenants',
    'corsheaders',
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

if DEBUG:
    INSTALLED_APPS += ['debug_toolbar']

TENANT_MODEL = 'tenants.Organization'
TENANT_DOMAIN_MODEL = 'tenants.Domain'
SHOW_PUBLIC_IF_NO_TENANT_FOUND = True

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    'shared.middleware.TenantResolverMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if DEBUG:
    MIDDLEWARE = ['debug_toolbar.middleware.DebugToolbarMiddleware'] + MIDDLEWARE
    INTERNAL_IPS = ['127.0.0.1']

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
        'NAME': env('DB_NAME'),
        'USER': env('DB_USER'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST'),
        'PORT': env('DB_PORT'),
        'CONN_MAX_AGE': 60,
    },
    # Read-only connection to the TherapyPMS source-of-truth database.
    # Django never migrates this DB — schema.
    'therapypms': {
        'ENGINE': 'apps.legacy.backend',
        'NAME': env('TPMS_DB_NAME'),
        'USER': env('TPMS_DB_USER'),
        'PASSWORD': env('TPMS_DB_PASSWORD'),
        'HOST': env('TPMS_DB_HOST'),
        'PORT': env('TPMS_DB_PORT'),
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
# Shared header value DocuSeal's form.completed webhook must send back —
# provisioned onto each Account's WebhookUrl in docuseal/app/models/account.rb.
DOCUSEAL_WEBHOOK_SECRET = env('DOCUSEAL_WEBHOOK_SECRET', default='')

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

AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='')
if not DEBUG and AWS_ACCESS_KEY_ID:
    DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
    AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='us-east-1')
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = 'private'

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

# CORS_ALLOWED_ORIGINS = env.list(
#     'CORS_ALLOWED_ORIGINS',
#     default=['http://localhost:5173', 'http://127.0.0.1:5173','https://app.progressly.io'],
# )
CORS_ALLOWED_ORIGINS = env.list(
    'CORS_ALLOWED_ORIGINS',
    default=[
        'http://localhost:3000',
        'http://127.0.0.1:3000',
        'http://localhost:5173',
        'http://127.0.0.1:5173',
        'https://app.progressly.io',
    ],
)
# CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=['https://api.progressly.io'])
# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'



# ---------------------------------------------------------------------------
# Sentry (production only — sentry-sdk is a production-only dependency,
# requirements/production.txt — import deferred inside this block so local
# dev, which doesn't install it, never tries to import it)
# ---------------------------------------------------------------------------

if not DEBUG:
    SENTRY_DSN = env('SENTRY_DSN', default='')
    if SENTRY_DSN:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration(), CeleryIntegration()],
            traces_sample_rate=0.1,
            send_default_pii=False,
        )

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

if DEBUG:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'handlers': {
            'console': {'class': 'logging.StreamHandler'},
            'ringbuffer': {'class': 'shared.log_buffer.RingBufferHandler'},
        },
        'loggers': {
            'django.db.backends': {
                'handlers': ['console'],
                'level': 'DEBUG',
            },
        },
        'root': {
            'handlers': ['console', 'ringbuffer'],
            'level': 'INFO',
        },
    }
else:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'json': {
                'format': '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}',
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'json',
            },
            # Backs GET /auth/admin/logs — see shared/log_buffer.py. Same
            'ringbuffer': {
                'class': 'shared.log_buffer.RingBufferHandler',
                'formatter': 'json',
            },
        },
        'root': {
            'handlers': ['console', 'ringbuffer'],
            'level': 'INFO',
        },
    }

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
