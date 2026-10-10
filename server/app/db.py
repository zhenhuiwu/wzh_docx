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

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction


@contextmanager
def write_transaction(connection: DictConnection) -> Iterator[DictConnection]:
    """BEGIN IMMEDIATE 写事务（01 §5.4）：开始即取得写锁，避免 DEFERRED 读后升级时直接 BUSY。"""
    if connection.in_transaction:
        raise RuntimeError("write_transaction 不能嵌套在已开始的事务里")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


def is_database_busy(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and (
        "database is locked" in str(error) or "database is busy" in str(error))


_journal_mode = ""


def journal_mode() -> str:
    """迁移时设置的日志模式（启动日志/健康检查用）。"""
    return _journal_mode


BUSY_TIMEOUT_MS = 5000      # 决策6：在线请求等待写锁最多 5 秒
MAINTENANCE_BUSY_MS = 30000  # 迁移、回填 CLI


def database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("DATABASE_URL must be sqlite:///...")
    return Path(url.removeprefix("sqlite:///"))


@contextmanager
def connect(busy_ms: int | None = None) -> Iterator[DictConnection]:
    busy_ms = BUSY_TIMEOUT_MS if busy_ms is None else busy_ms
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("DATABASE_URL must be sqlite:///...")
    path = Path(url.removeprefix("sqlite:///"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=busy_ms / 1000)
    connection.row_factory = Row
    connection.execute("pragma foreign_keys = on")
    connection.execute(f"pragma busy_timeout = {int(busy_ms)}")
    connection.execute("pragma synchronous = FULL")
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
    global _journal_mode
    applied: list[str] = []
    with connect(MAINTENANCE_BUSY_MS) as connection:
        # WAL 持久化在库文件里；必须在事务外切换，所以不能写进迁移文件（01 §2.3）。
        _journal_mode = str(connection.execute("pragma journal_mode = WAL").fetchone()[0]).lower()
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
            # 审查 S10：成员补齐迁移前后各留一份清单（01 §3.3）
            report_before = _members_before(connection, path.stem)
            try:
                connection._conn.executescript(
                    "BEGIN;\n" + path.read_text(encoding="utf-8")
                    + f"\ninsert into schema_migrations (version) values ('{path.stem}');\nCOMMIT;"
                )
            except sqlite3.Error:
                connection.rollback()
                raise
            if report_before is not None:
                _members_after(connection, path.stem, report_before)
            applied.append(path.name)
    return applied


_MEMBER_REPORT_MIGRATIONS = ("110_",)


def _members_before(connection: DictConnection, version: str) -> dict | None:
    if not version.startswith(_MEMBER_REPORT_MIGRATIONS):
        return None
    from app.members_report import members_snapshot
    try:
        return members_snapshot(connection._conn)
    except sqlite3.Error:
        return None  # 空库（projects 表还不存在）不出报告


def _members_after(connection: DictConnection, version: str, before: dict) -> None:
    from app.members_report import members_snapshot, write_migration_report
    if not before["projects"]:
        return  # 新库没有项目，无需核对
    url = get_settings().database_url
    directory = Path(url.removeprefix("sqlite:///")).parent if url.startswith("sqlite:///") else Path(".")
    write_migration_report(directory, version, before, members_snapshot(connection._conn))
