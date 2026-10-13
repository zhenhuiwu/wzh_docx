from app.db import connect, database_path, migrate
from app.rollback_migration_119 import rollback


def test_rollback_119_empty_then_reapply():
    migrate()
    path = database_path()
    assert rollback(path, apply=True) == 0
    with connect() as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='document_baselines'").fetchone() is None
    assert migrate() == ["119_document_baselines.sql"]
