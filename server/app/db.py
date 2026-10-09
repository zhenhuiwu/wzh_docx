"""SQLite connections and ordered SQLite migrations."""

from __future__ import annotations

import re
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from app.config import get_settings


def migrations_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "migrations"


class Row(Mapping[str, object]):
    def __init__(self, cursor: sqlite3.Cursor, values: tuple[object, ...]):
        self.names = [column[0] for column in cursor.description]
        self.values = tuple(self._convert(name, value) for name, value in zip(self.names, values))

    @staticmethod
    def _convert(name: str, value: object) -> object:
        if not isinstance(value, str):
            return value
        if name in {"format_template", "outline", "content", "blocks"}:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        if name == "id" or name.endswith("_id"):
            try:
                return UUID(value)
            except ValueError:
                return value
        if name in {"period_start", "period_end"}:
            return date.fromisoformat(value)
        if name.endswith("_at") or name in {"lease_until", "window_started_at"}:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return value

    def __getitem__(self, key: str | int) -> object:
        return self.values[key if isinstance(key, int) else self.names.index(key)]

    def __iter__(self):
        return iter(self.names)

    def __len__(self) -> int:
        return len(self.names)


sqlite3.register_adapter(UUID, str)
sqlite3.register_adapter(datetime, lambda value: value.isoformat())
sqlite3.register_adapter(date, lambda value: value.isoformat())


class DictConnection:
    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection

    def execute(self, sql: str, params=()):
        sql = sql.replace("%s", "?")
        sql = re.sub(r"\bilike\b", "like", sql, flags=re.IGNORECASE)
        sql = re.sub(r"::uuid\b", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bfor update(?: skip locked)?\b", "", sql, flags=re.IGNORECASE)
        return self._conn.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def transaction(self, *, immediate: bool = False):
        if immediate:
            self.execute("BEGIN IMMEDIATE")
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _traceback):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        return False


@contextmanager
def connect() -> Iterator[DictConnection]:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("DATABASE_URL must be sqlite:///...")
    path = Path(url.removeprefix("sqlite:///"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = Row
    connection.execute("pragma foreign_keys = on")
    try:
        yield DictConnection(connection)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def ping() -> bool:
    try:
        with connect() as connection:
            connection.execute("select 1")
        return True
    except sqlite3.Error:
        return False


def migrate() -> list[str]:
    """Legacy 001–035 migrations are PostgreSQL only; apply SQLite 100+ scripts."""
    applied: list[str] = []
    with connect() as connection:
        connection.execute(
            "create table if not exists schema_migrations ("
            "version text primary key, applied_at text not null default CURRENT_TIMESTAMP)"
        )
        connection.commit()
        for path in sorted(migrations_dir().glob("*.sql")):
            if int(path.stem.split("_", 1)[0]) < 100:
                continue
            if connection.execute(
                "select 1 from schema_migrations where version = ?", (path.stem,)
            ).fetchone():
                continue
            try:
                connection._conn.executescript(
                    "BEGIN;\n" + path.read_text(encoding="utf-8")
                    + f"\ninsert into schema_migrations (version) values ('{path.stem}');\nCOMMIT;"
                )
            except sqlite3.Error:
                connection.rollback()
                raise
            applied.append(path.name)
    return applied
