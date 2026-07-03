"""
Database router for unmanaged TherapyPMS models.
All reads for the `legacy` app label go to the `therapypms` connection.
Writes and migrations are blocked — the schema is owned by the Laravel app.
"""

_LEGACY_APP = 'legacy'
_TPMS_DB = 'therapypms'


class TherapyPmsRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == _LEGACY_APP:
            return _TPMS_DB
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == _LEGACY_APP:
            return None  # never write through Django
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == _LEGACY_APP:
            return False  # never create/drop TPMS tables via Django migrations
        return None
