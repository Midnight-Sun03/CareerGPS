from django.apps import AppConfig


def _enable_sqlite_wal(sender, connection, **kwargs):
    """Switch SQLite connections to WAL as soon as they are created.

    Rollback-journal mode lets a reader's SHARED lock abort a writer's COMMIT
    (and vice versa), which is exactly how concurrent request/drain threads
    produced OperationalError("database is locked") in production runs.  WAL
    removes that reader-vs-writer blocking entirely; writer-vs-writer
    serialization is handled by the connection busy timeout set in settings.
    Idempotent: journal_mode is persistent per database file, and on
    in-memory test databases it simply reports "memory" back.
    """
    if connection.vendor != 'sqlite':
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')
    except Exception:  # noqa: BLE001 - never block a connection over a PRAGMA
        pass


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from core.services import integrity  # noqa: F401
        from core import checks  # noqa: F401
        from django.db.backends.signals import connection_created
        connection_created.connect(
            _enable_sqlite_wal, dispatch_uid='core.sqlite_wal')