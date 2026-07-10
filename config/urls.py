from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from api.v1 import api
from shared.log_buffer import logs_view

urlpatterns = [
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
