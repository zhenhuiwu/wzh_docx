"""Run with server/.venv/Scripts/python.exe deploy/test_snapshot_data.py."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from snapshot_data import env_example, env_quote, snapshot


def check_env_quoting() -> None:
    from io import StringIO

    from dotenv import dotenv_values

    values = {
        "PLAIN": r"D:\数据\files",
        "QUOTE": "it's \"quoted\" \\'",
        "DOLLAR": "$HOME/x $USER {braces}",
        "MULTI": "第一行\n第二行\r\n\tC:\\path \"q\"",
        "TRAILING": "ends with backslash\\",
        "AI_API_KEY": "must-not-appear",
    }
    text = env_example(values)
    assert "must-not-appear" not in text
    parsed = dotenv_values(stream=StringIO(text))
    for key, value in values.items():
        if key != "AI_API_KEY":
            assert parsed[key] == value, (key, parsed[key], value)
    assert parsed["AI_API_KEY"] == ""
    try:
        env_quote("${HOME}/data")
    except ValueError:
        pass
    else:
        raise AssertionError("${...} must be refused")


def check() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        database, files, env_file = source / "app.db", source / "files", source / ".env"
        (files / "objects").mkdir(parents=True)
        (files / "test-runs").mkdir()
        (files / "tmp").mkdir()
        content = b"original Word object"
        (files / "objects" / "saved-object").write_bytes(content)
        (files / "test-runs" / "screenshot.png").write_bytes(b"test evidence")
        (files / "tmp" / "upload.part").write_bytes(b"unfinished")
        env_file.write_text("AI_API_KEY='do-not-print-this'\nAI_REASONING_EFFORT='high'\nDATABASE_URL='sqlite:///old.db'\nFILE_DATA_DIR='old-files'\n", encoding="utf-8")
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("""
                CREATE TABLE documents(id TEXT PRIMARY KEY, markdown_content TEXT);
                INSERT INTO documents VALUES('document','preserved writing');
                CREATE TABLE users(id TEXT PRIMARY KEY);
                INSERT INTO users VALUES('admin');
                CREATE TABLE user_roles(user_id TEXT REFERENCES users(id),role TEXT);
                INSERT INTO user_roles VALUES('admin','system_admin'),('old-missing-user','system_admin');
                CREATE TABLE files(storage_key TEXT,size_bytes INTEGER,sha256 TEXT,status TEXT);
                CREATE TABLE sessions(id TEXT); INSERT INTO sessions VALUES('session');
                CREATE TABLE csrf_challenges(id TEXT); INSERT INTO csrf_challenges VALUES('challenge');
                CREATE TABLE login_guards(id TEXT); INSERT INTO login_guards VALUES('guard');
                CREATE TABLE idempotency_keys(id TEXT); INSERT INTO idempotency_keys VALUES('request');
                CREATE TABLE writing_skill_requests(id TEXT); INSERT INTO writing_skill_requests VALUES('skill-request');
                CREATE TABLE test_tasks(id TEXT PRIMARY KEY,status TEXT,config_json TEXT);
                INSERT INTO test_tasks VALUES('task','ready','{"frontendUrl":"http://x","testUsername":"tester","testPassword":"tester-secret-123","nested":{"apiKey":"nested-key-456"}}');
                CREATE TABLE test_task_runs(id TEXT,status TEXT,snapshot_json TEXT,result_json TEXT);
                INSERT INTO test_task_runs VALUES('run','completed','{"testPassword":"tester-secret-123","cases":[]}','{"log":"typed tester-secret-123 into field"}');
            """)
            connection.execute("INSERT INTO files VALUES(?,?,?,'stored')", ("saved-object", len(content), hashlib.sha256(content).hexdigest()))
            connection.commit()
        original_database, original_env = database.read_bytes(), env_file.read_bytes()
        inspection = snapshot(database, files, env_file, root / "inspect", execute=False)
        assert inspection["file_count"] == 2 and not (root / "inspect").exists()
        output = root / "package"
        metadata = snapshot(database, files, env_file, output, execute=True,
                            env_updates={"AI_MODEL": "model-x", "AI_REASONING_EFFORT": "high",
                                         "AI_API_KEY": "do-not-print-this", "BOOTSTRAP_ADMIN_PASSWORD": "do-not-print-this"})
        assert database.read_bytes() == original_database and env_file.read_bytes() == original_env
        assert (output / "data/files/test-runs/screenshot.png").read_bytes() == b"test evidence"
        assert not (output / "data/files/tmp").exists()
        with closing(sqlite3.connect(output / "data/app.db")) as copied:
            assert copied.execute("SELECT markdown_content FROM documents").fetchone()[0] == "preserved writing"
            assert all(copied.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
                       for table in metadata["cleared_transient_tables"])
            assert copied.execute("SELECT * FROM user_roles").fetchall() == [('admin','system_admin')]
            assert copied.execute("PRAGMA foreign_key_check").fetchall() == []
        assert metadata["discarded_orphan_user_roles"] == 1
        assert metadata["source_business_counts"]["user_roles"] == 2 and metadata["business_counts"]["user_roles"] == 1
        assert not (output / ".env").exists()
        env_copy = (output / ".env.example").read_text(encoding="utf-8")
        assert "do-not-print-this" not in env_copy
        assert "sqlite:///./data/app.db" in env_copy and "./data/files" in env_copy
        assert "AI_REASONING_EFFORT='high'" in env_copy and "AI_MODEL='model-x'" in env_copy
        assert "AI_API_KEY=''" in env_copy
        leaked = [path for path in output.rglob("*") if path.is_file()
                  for secret in (b"do-not-print-this", "tester-secret-123".encode(), b"nested-key-456")
                  if secret in path.read_bytes()]
        assert not leaked, leaked
        with closing(sqlite3.connect(output / "data/app.db")) as copied:
            config = json.loads(copied.execute("SELECT config_json FROM test_tasks").fetchone()[0])
            assert "testPassword" not in config and config["testUsername"] == "tester"
            assert "apiKey" not in config["nested"]
            snapshot_json, result_json = copied.execute("SELECT snapshot_json,result_json FROM test_task_runs").fetchone()
            assert "testPassword" not in json.loads(snapshot_json)
            assert "******" in result_json
        assert metadata["scrubbed_credentials"] == {
            "credentials_removed": 3,
            "keys_removed": {"test_task_runs.snapshot_json": 1, "test_tasks.config_json": 2},
            "masked_occurrences": {"test_task_runs.result_json": 1},
        }
        source_config = json.loads(sqlite3.connect(database).execute("SELECT config_json FROM test_tasks").fetchone()[0])
        assert source_config["testPassword"] == "tester-secret-123"
        assert "do-not-print-this" not in json.dumps(metadata)
        try:
            snapshot(database, files, env_file, output, execute=True)
        except RuntimeError as error:
            assert "拒绝覆盖" in str(error)
        else:
            raise AssertionError("Existing data must not be overwritten")
        import snapshot_data
        copy2 = snapshot_data.shutil.copy2
        def change_during_copy(*args, **kwargs):
            copied = copy2(*args, **kwargs)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE documents SET markdown_content='concurrent edit'")
                connection.commit()
            return copied
        changed = root / "concurrent"
        with patch.object(snapshot_data.shutil, "copy2", change_during_copy):
            try:
                snapshot(database, files, env_file, changed, execute=True)
            except RuntimeError as error:
                assert "发生变化" in str(error)
            else:
                raise AssertionError("Concurrent edits must prevent publishing")
        assert not (changed / "data").exists() and not (changed / ".env.example").exists()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE test_task_runs SET status='running'")
            connection.commit()
        try:
            snapshot(database, files, env_file, root / "busy", execute=True)
        except RuntimeError as error:
            assert "任务正在运行" in str(error)
        else:
            raise AssertionError("Running tests must prevent snapshotting")
    print("snapshot check passed: preserved data/evidence, shipped .env.example without secrets, cleared copied sessions, refused overwrite/concurrent writes/running tasks")


def _weak_source(root: Path, password: str, document: str, account: str) -> tuple[Path, Path, Path]:
    source = root / "source"
    (source / "files" / "objects").mkdir(parents=True)
    env_file = source / ".env"
    env_file.write_text("", encoding="utf-8")
    database = source / "app.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("""
            CREATE TABLE documents(id TEXT PRIMARY KEY, markdown_content TEXT);
            CREATE TABLE users(id TEXT PRIMARY KEY, account TEXT, account_key TEXT, password_hash TEXT);
            CREATE TABLE files(storage_key TEXT,size_bytes INTEGER,sha256 TEXT,status TEXT);
            CREATE TABLE test_tasks(id TEXT PRIMARY KEY,status TEXT,config_json TEXT,cases_json TEXT,error TEXT);
            CREATE TABLE test_task_runs(id TEXT,status TEXT,snapshot_json TEXT,result_json TEXT);
        """)
        config = json.dumps({"testUsername": "tester", "testPassword": password})
        connection.execute("INSERT INTO documents VALUES('doc',?)", (document,))
        connection.execute("INSERT INTO users VALUES('u1',?,?,?)", (account, account.lower(), f"pbkdf2$x{password}y$z"))
        connection.execute("INSERT INTO test_tasks VALUES('task','ready',?,'[]','')", (config,))
        connection.execute("INSERT INTO test_task_runs VALUES('run','completed',?,?)",
                           (config, json.dumps({"log": f"fill {password}"})))
        connection.commit()
    return database, source / "files", env_file


def check_weak_passwords() -> None:
    import snapshot_data
    from snapshot_data import CredentialLeftError

    cases = [
        ("123456", "产品编号 123456", "writer", "documents.markdown_content"),
        ("zhangsan", "普通正文", "zhangsan", "users.account"),
    ]
    for password, document, account, where in cases:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database, files, env_file = _weak_source(root, password, document, account)
            original = database.read_bytes()
            try:
                snapshot(database, files, env_file, root / "package", execute=True)
            except CredentialLeftError as error:
                assert where in str(error), str(error)
            else:
                raise AssertionError(f"{where} must stop the migration")
            assert database.read_bytes() == original
            assert not (root / "package" / "data").exists()

    # Not a whole token, or only inside a hash: business data is shipped unchanged.
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database, files, env_file = _weak_source(root, "123456", "订单 A1234567 与 1234567", "writer")
        metadata = snapshot(database, files, env_file, root / "package", execute=True)
        with closing(sqlite3.connect(root / "package/data/app.db")) as copied, closing(sqlite3.connect(database)) as source:
            for query in ("SELECT * FROM documents", "SELECT * FROM users"):
                assert copied.execute(query).fetchall() == source.execute(query).fetchall()
            assert "123456" not in copied.execute("SELECT result_json FROM test_task_runs").fetchone()[0]
        assert metadata["scrubbed_credentials"]["masked_occurrences"] == {"test_task_runs.result_json": 1}

    # A scrub that touched another table is caught by the integrity check.
    original_scrub = snapshot_data.scrub_credentials

    def overreaching(connection):
        result = original_scrub(connection)
        connection.execute("UPDATE documents SET markdown_content='changed'")
        return result

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database, files, env_file = _weak_source(root, "strong-pass-123", "正文", "writer")
        with patch.object(snapshot_data, "scrub_credentials", overreaching):
            try:
                snapshot(database, files, env_file, root / "package", execute=True)
            except RuntimeError as error:
                assert "测试执行记录以外" in str(error), str(error)
            else:
                raise AssertionError("integrity check must catch collateral changes")
        assert not (root / "package" / "data").exists()
    print("weak password check passed: leftovers outside test tables stop the migration, business tables unchanged")


if __name__ == "__main__":
    check_env_quoting()
    check_weak_passwords()
    check()
