from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = 'apps.integrations'
    label = 'integrations'
    verbose_name = 'Integrations'

    def ready(self):
        from .practice_planner.audit import connect_audit
        connect_audit()
