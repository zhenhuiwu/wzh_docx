from app.db import connect, database_path, migrate
from app.rollback_migration_119 import rollback
from tests.migration_chain import files_from, rollback_newer_than


def test_rollback_119_empty_then_reapply():
    migrate()
    path = database_path()
    assert rollback_newer_than(path, 119) == 0
    assert rollback(path, apply=True) == 0
    with connect() as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='document_baselines'").fetchone() is None
    assert migrate() == files_from(119)


def test_rollback_119_refuses_while_newer_present():
    migrate()
    if len(files_from(119)) > 1:
        assert rollback(database_path(), apply=True) == 2
