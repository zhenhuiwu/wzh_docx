"""Foundation acceptance: migration, health, and closed file access."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, clear_settings_cache
from app.db import connect, migrate
from app.errors import AppError
from app.files import read_object_bytes

SERVER_ROOT = Path(__file__).resolve().parents[1]
_PROCESS_SCRIPT = """
import os
from pathlib import Path

from app.db import migrate
from app.files import load_record, store_bytes

migrate()
result = Path(os.environ["RESULT"])
if os.environ["PHASE"] == "write":
    record = store_bytes(
        name="note.txt",
        media_type="text/plain",
        purpose="attachment",
        content=b"persist-me",
    )
    result.write_text(str(record.id), encoding="utf-8")
else:
    record = load_record(result.read_text(encoding="utf-8"))
    if record is None or record.name != "note.txt":
        raise SystemExit(2)
    public = record.public_dict()
    if "storage_key" in public or "storageKey" in public:
        raise SystemExit(3)
    print(public["name"])
    print(public["status"])
    print(public["sha256"])
"""


@pytest.fixture()
def runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        pytest.fail("DATABASE_URL is required and has no default")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path))
    clear_settings_cache()
    migrate()
    yield database_url, tmp_path
    clear_settings_cache()


@pytest.fixture()
def client(runtime_env):
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_settings_supply_local_sqlite_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("FILE_DATA_DIR", raising=False)
    clear_settings_cache()
    settings = Settings(_env_file=None)
    assert settings.database_url == "sqlite:///./data/app.db"
    assert settings.file_data_dir == Path("./data/files")
    assert settings.ai_api_key == ""
    clear_settings_cache()


def test_migrate_empty_database_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'migrations.db'}")
    clear_settings_cache()
    first = migrate()
    second = migrate()
    assert first[0] == "100_sqlite_init.sql"
    assert "101_sqlite_runtime.sql" in first
    assert second == []
    with connect() as connection:
        rows = connection.execute(
            "pragma table_info(files)"
        ).fetchall()
    names = {row["name"] for row in rows}
    assert "storage_key" in names
    assert "sha256" in names


def test_file_metadata_survives_new_process(runtime_env) -> None:
    database_url, directory = runtime_env
    result = directory / "result-id.txt"
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["FILE_DATA_DIR"] = str(directory)
    env["PYTHONPATH"] = str(SERVER_ROOT)
    env["RESULT"] = str(result)
    env["PHASE"] = "write"
    write = subprocess.run(
        [sys.executable, "-c", _PROCESS_SCRIPT],
        cwd=SERVER_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert write.returncode == 0, write.stderr
    env["PHASE"] = "read"
    read = subprocess.run(
        [sys.executable, "-c", _PROCESS_SCRIPT],
        cwd=SERVER_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert read.returncode == 0, read.stderr
    name, status, digest = read.stdout.splitlines()
    assert name == "note.txt"
    assert status == "stored"
    assert len(digest) == 64
    stored = list((directory / "objects").iterdir())
    assert stored
    assert all(path.name != "note.txt" for path in stored)
    assert database_url not in read.stdout


def test_health_omits_secrets(client: TestClient, runtime_env) -> None:
    database_url, directory = runtime_env
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"data", "requestId"}
    assert set(body["data"]) == {"status", "timestamp"}
    assert body["data"]["status"] == "ok"
    assert body["requestId"]
    assert response.headers["x-request-id"] == body["requestId"]
    assert database_url not in response.text
    assert str(directory) not in response.text
    assert "storage_key" not in response.text
    assert "Traceback" not in response.text


def test_health_degraded_still_omits_secrets(
    client: TestClient,
    runtime_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, directory = runtime_env
    monkeypatch.setattr("app.main.ping", lambda: False)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "degraded"
    assert database_url not in response.text
    assert str(directory) not in response.text


def test_internal_error_hides_details(
    client: TestClient,
    runtime_env,
) -> None:
    database_url, _directory = runtime_env

    @client.app.get("/boom")
    def boom() -> None:
        raise RuntimeError(database_url)

    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert database_url not in response.text
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text


def _file_count() -> int:
    with connect() as connection:
        row = connection.execute("select count(*) as total from files").fetchone()
    assert row is not None
    return int(row["total"])


def _tree_bytes(root: Path) -> bytes:
    chunks: list[bytes] = []
    if not root.exists():
        return b""
    for path in root.rglob("*"):
        if path.is_file():
            chunks.append(path.read_bytes())
    return b"".join(chunks)


def test_unauthenticated_upload_writes_nothing(
    client: TestClient,
    runtime_env,
) -> None:
    _database_url, directory = runtime_env
    marker = b"do-not-persist-this-upload"
    outside = directory.parent / "traversal-outside.txt"
    before = _file_count()
    client.cookies.set("docwb_session", "forged-session")
    response = client.post(
        "/api/v1/files",
        files={"file": ("../../traversal-outside.txt", marker, "text/plain")},
        data={"purpose": "attachment"},
        headers={"Idempotency-Key": "upload-once"},
    )
    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"error", "requestId"}
    assert body["error"]["code"] == "AUTH_REQUIRED"
    assert "forged-session" not in response.text
    assert _file_count() == before
    assert marker not in _tree_bytes(directory)
    assert not outside.exists()


def test_path_and_unauthenticated_reads_are_rejected(
    client: TestClient,
    runtime_env,
) -> None:
    _database_url, directory = runtime_env
    secret = directory.parent / "outside-secret.txt"
    secret.write_bytes(b"outside-secret-marker")
    try:
        with pytest.raises(AppError):
            read_object_bytes(directory, "../outside-secret.txt")
        with pytest.raises(AppError):
            read_object_bytes(directory, "..\\outside-secret.txt")
        with pytest.raises(AppError):
            read_object_bytes(directory, str(secret))
        invalid = client.get("/api/v1/files/not-a-uuid/content")
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "VALIDATION_FAILED"
        encoded = client.get("/api/v1/files/%2e%2e%2f%2e%2e%2fwin.ini/content")
        assert encoded.status_code in {404, 422}
        assert b"outside-secret-marker" not in encoded.content
        injected = client.get(
            "/api/v1/files/%27%3Bdrop%20table%20files%3B--/content"
        )
        assert injected.status_code == 422
        preview = client.get("/api/v1/files/not-a-uuid/preview")
        assert preview.status_code == 422
        file_id = "00000000-0000-4000-8000-000000000001"
        client.cookies.set("docwb_session", "forged-session")
        denied = client.get(f"/api/v1/files/{file_id}/content")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "AUTH_REQUIRED"
        assert b"outside-secret-marker" not in denied.content
        preview_denied = client.get(f"/api/v1/files/{file_id}/preview")
        assert preview_denied.status_code == 401
        with connect() as connection:
            row = connection.execute(
                "select name from sqlite_master where type = 'table' and name = 'files'"
            ).fetchone()
        assert row is not None
        assert row["name"] == "files"
    finally:
        secret.unlink(missing_ok=True)


@pytest.mark.parametrize("name,fallback", [
    ("report.docx", "report.docx"),
    ('evil"\r\nSet-Cookie: x=1.docx', "evil___Set-Cookie: x=1.docx"),
    ("a\\b.pdf", "a_b.pdf"),
    ("需求规格说明.docx", "download.docx"),
    ("\x00\x01", "__"),
    ("...", "download"),
])
def test_content_disposition_never_echoes_unsafe_characters(name, fallback):
    from app.files import content_disposition

    header = content_disposition("attachment", name)
    assert "\r" not in header and "\n" not in header and "\x00" not in header
    assert header.startswith(f'attachment; filename="{fallback}"; filename*=UTF-8\'\'')
