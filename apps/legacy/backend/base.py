"""
Custom PostgreSQL backend for the TherapyPMS read-only connection.
Overrides the Django 5 minimum-version check so we can connect to the
TPMS server which runs PostgreSQL 12 (Django 5 requires PG 13+).
"""
from django.db.backends.postgresql.base import DatabaseWrapper as PgDatabaseWrapper


class DatabaseWrapper(PgDatabaseWrapper):
    def check_database_version_supported(self):
        pass  # TPMS runs PG 12 — skip the PG 13+ gate
