from django.apps import AppConfig


class ClientsConfig(AppConfig):
    name = 'apps.clients'
    label = 'clients'
    verbose_name = 'Clients'

    def ready(self):
        from . import status_history  # noqa: F401
