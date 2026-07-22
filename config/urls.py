import time
import django
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.http import JsonResponse
from django.db import connection
from api.v1 import api
from shared.log_buffer import logs_view


def health(request):
    checks = {}
    http_status = 200

    try:
        t = time.monotonic()
        connection.ensure_connection()
        checks['db'] = {'status': 'ok', 'latency_ms': round((time.monotonic() - t) * 1000, 2)}
    except Exception as e:
        checks['db'] = {'status': 'error', 'detail': str(e)}
        http_status = 503

    try:
        from django.core.cache import cache
        t = time.monotonic()
        cache.set('_health', 1, timeout=5)
        assert cache.get('_health') == 1
        checks['cache'] = {'status': 'ok', 'latency_ms': round((time.monotonic() - t) * 1000, 2)}
    except Exception as e:
        checks['cache'] = {'status': 'error', 'detail': str(e)}
        http_status = 503

    return JsonResponse({
        'status': 'ok' if http_status == 200 else 'degraded',
        'django': django.get_version(),
        'debug': settings.DEBUG,
        'checks': checks,
    }, status=http_status)


urlpatterns = [
    path('', health),
    path('internal/logs/', logs_view),
    path('admin/', admin.site.urls),
    path('api/v1/', api.urls),
]

if settings.DEBUG:
    try:
        import debug_toolbar
        urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    except ImportError:
        pass

    # Serve user-uploaded media locally (S3 handles this in production —
    # see DEFAULT_FILE_STORAGE in settings/production.py).
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
