"""三审：迁移 114 可单独回滚，回滚后重新迁移能再加回来。"""
from app.rollback_migration_118 import rollback as _rb118_only  # A07：118 在 117 之后，先回滚
from app.rollback_migration_120 import rollback as _rb120  # 任务与模板：120 在 118 之后


def _rb118(path, apply=False, force=False):
    """按 120 → 118 的顺序回滚（120 失败时原样返回其退出码）。"""
    code = _rb120(path, apply=apply, force=force)
    return code if code else _rb118_only(path, apply=apply, force=force)

import sqlite3

from app.rollback_migration_114 import COLUMN, VERSION, rollback


def _migrate():
    """迁移到最新后倒序回滚 116、115（114 回滚要求更新的迁移都已回滚）。"""
    from app.db import migrate
    from app.rollback_migration_115 import rollback as rollback_115
    from app.rollback_migration_116 import rollback as rollback_116
    from app.rollback_migration_117 import rollback as rollback_117

    migrate()
    from app.rollback_migration_114 import _database_path

    assert _rb118(_database_path(), apply=True) == 0 and rollback_117(_database_path(), apply=True) == 0 and rollback_116(_database_path(), apply=True) == 0
    assert rollback_115(_database_path(), apply=True) == 0


def _columns(path):
    with sqlite3.connect(path) as connection:
        return {row[1] for row in connection.execute("pragma table_info(document_objects)")}


def _recorded(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("select count(*) from schema_migrations where version = ?", (VERSION,)).fetchone()[0]


def test_rollback_114_dry_run_then_apply_then_reapply(tmp_path, monkeypatch, capsys):
    from app import config

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        _migrate()
        assert COLUMN in _columns(path) and _recorded(path) == 1
        assert rollback(path, apply=False) == 0
        assert COLUMN in _columns(path) and _recorded(path) == 1  # 只检查不改库
        assert rollback(path, apply=True) == 0
        assert COLUMN not in _columns(path) and _recorded(path) == 0
        assert list(tmp_path.glob("app.db.before-rollback-114-*.db"))
        assert rollback(path, apply=True) == 0
        assert "不需要回滚" in capsys.readouterr().out
        _migrate()
        assert COLUMN in _columns(path) and _recorded(path) == 1
    finally:
        config.get_settings.cache_clear()


def test_rollback_114_refuses_while_service_running(tmp_path, monkeypatch, capsys):
    """终验回归 M1：服务持有运行标记时 --yes 拒绝执行（退出码 2），库不变；停服务后可回滚。"""
    import subprocess
    import sys
    import time

    from app import config, service_lock

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        _migrate()
        # 另起一个进程模拟正在运行的服务（持有运行标记）
        holder = subprocess.Popen([sys.executable, "-c", (
            "import sys, time; from pathlib import Path; from app import service_lock; "
            f"service_lock.acquire(Path({str(path)!r})); print('ready', flush=True); time.sleep(60)")],
            stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "ready"
            assert service_lock.service_running(path)
            assert rollback(path, apply=True) == 2
            assert "请先停止" in capsys.readouterr().err
            assert COLUMN in _columns(path) and _recorded(path) == 1
            assert rollback(path, apply=False) == 0  # 只检查仍可用，并给出提醒
            assert "请先停止" in capsys.readouterr().err
        finally:
            holder.terminate()
            holder.wait(10)
        # Windows 进程退出信号到达后，系统可能仍在释放文件锁。
        deadline = time.monotonic() + 1
        while service_lock.service_running(path) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not service_lock.service_running(path)
        assert rollback(path, apply=True) == 0
        assert "旧版本程序" in capsys.readouterr().out
        assert COLUMN not in _columns(path)
    finally:
        config.get_settings.cache_clear()


def test_open_connection_counts_as_in_use(tmp_path, monkeypatch):
    """Linux：没有运行标记但有其他进程打开着库（如旧版本程序）也拒绝。"""
    import subprocess
    import sys

    from app import config, service_lock

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        _migrate()
        holder = subprocess.Popen([sys.executable, "-c", (
            "import sqlite3, time; c = sqlite3.connect(" + repr(str(path)) + "); c.execute('select 1 from document_objects limit 1').fetchall(); "
            "print('ready', flush=True); time.sleep(60)")], stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "ready"
            if not service_lock.processes_with_file(path):
                return  # 非 Linux 或看不到其他进程
            assert rollback(path, apply=True) == 2
            assert COLUMN in _columns(path)
        finally:
            holder.terminate()
            holder.wait(10)
    finally:
        config.get_settings.cache_clear()


def test_main_requires_manual_confirmation(tmp_path, monkeypatch, capsys):
    """终审建议：旧版本服务查不出来，--yes 非交互时必须另加 --service-stopped，否则不改库。"""
    from app import config
    from app.rollback_migration_114 import main

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        _migrate()
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        assert main(["--yes"]) == 2
        assert "--service-stopped" in capsys.readouterr().err
        assert COLUMN in _columns(path)
        assert main(["--yes", "--service-stopped"]) == 0
        assert COLUMN not in _columns(path)
    finally:
        config.get_settings.cache_clear()


def test_main_refuses_before_asking_yes_when_service_running(tmp_path, monkeypatch, capsys):
    """终验可顺手做：检测得到服务在运行时，在要求输入 yes 之前就拒绝（不调用 input）。"""
    import subprocess
    import sys

    from app import config
    from app.rollback_migration_114 import main

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        _migrate()
        holder = subprocess.Popen([sys.executable, "-c", (
            "import time; from pathlib import Path; from app import service_lock; "
            f"service_lock.acquire(Path({str(path)!r})); print('ready', flush=True); time.sleep(60)")],
            stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "ready"
            monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
            asked = []
            monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "yes")
            assert main(["--yes"]) == 2
            assert asked == []
            assert "请先停止" in capsys.readouterr().err
            assert COLUMN in _columns(path)
        finally:
            holder.terminate()
            holder.wait(10)
    finally:
        config.get_settings.cache_clear()


def test_rollback_114_refuses_while_115_present(tmp_path, monkeypatch, capsys):
    """测试工程师：115 的表还在时拒绝回滚 114（退出码 2），提示先回滚 115；库不变。"""
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        from app.rollback_migration_116 import rollback as rollback_116
        from app.rollback_migration_117 import rollback as rollback_117

        assert _rb118(path, apply=True) == 0 and rollback_117(path, apply=True) == 0 and rollback_116(path, apply=True) == 0
        assert rollback(path, apply=True) == 2
        assert "rollback_migration_115" in capsys.readouterr().err
        assert COLUMN in _columns(path) and _recorded(path) == 1
        assert not list(tmp_path.glob("app.db.before-rollback-114-*.db"))
    finally:
        config.get_settings.cache_clear()


def test_rollback_114_refuses_while_115_or_116_present(tmp_path, monkeypatch, capsys):
    """测试 20 M-1：115 与 116 都在时拒绝并提示从 116 开始（116 → 115 → 114）；
    115 的记录和表被手工去掉、只剩 116 时（mig116.sh C3 的中间态）也拒绝，提示先回滚 116。"""
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        from app.rollback_migration_117 import rollback as _rollback_117
        assert _rb118(path, apply=True) == 0 and _rollback_117(path, apply=True) == 0  # A06 的 117 在 116 之后，先回滚它
        assert rollback(path, apply=True) == 2
        err = capsys.readouterr().err
        assert "rollback_migration_116" in err and "116 → 115 → 114" in err
        with sqlite3.connect(path) as connection:
            connection.execute("drop table document_conflict_mine_snapshots")
            connection.execute("delete from schema_migrations where version like '115%'")
        assert rollback(path, apply=False) == 2
        err = capsys.readouterr().err
        assert "rollback_migration_116" in err and "116 → 114" in err
        assert COLUMN in _columns(path) and _recorded(path) == 1
        assert not list(tmp_path.glob("app.db.before-rollback-114-*.db"))
    finally:
        config.get_settings.cache_clear()
