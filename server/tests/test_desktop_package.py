"""One portable-entry check: routes, the random initial account and existing data."""
import os
import stat
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import desktop
from app.config import clear_settings_cache
from app.db import connect
from app.identity import verify_password


def test_portable_routes_and_initial_account(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<title>文档工作室</title>", encoding="utf-8")
    app = FastAPI()
    app.mount("/", desktop.FrontendFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        assert client.get("/login?redirect=/projects").status_code == 200
        assert client.get("/projects/a/documents/b").status_code == 200
        assert client.get("/missing.js").status_code == 404
        assert client.get("/api/v1/missing").status_code == 404
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "app.db"))
    monkeypatch.setenv("DOCWB_INITIAL_PASSWORD_FILE", "")
    clear_settings_cache()
    try:
        desktop.initialize_admin(tmp_path)
        secret_file = tmp_path / desktop.INITIAL_PASSWORD_FILE
        lines = secret_file.read_text(encoding="utf-8-sig").splitlines()
        password = next(line.split("：", 1)[1] for line in lines if line.startswith("初始密码"))
        assert password != "admin123" and len(password) >= 12
        with connect() as connection:
            original = dict(connection.execute("SELECT * FROM users").fetchone())
            assert original["account"] == "admin"
            assert original["must_change_password"]
            assert verify_password(password, original["password_hash"])
            assert not verify_password("admin123", original["password_hash"])
        if os.name == "posix":
            assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
        secret_file.unlink()
        desktop.initialize_admin(tmp_path)
        assert not secret_file.exists()
        with connect() as connection:
            assert dict(connection.execute("SELECT * FROM users").fetchone()) == original
    finally:
        clear_settings_cache()


@pytest.fixture()
def empty_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "app.db"))
    monkeypatch.setenv("DOCWB_INITIAL_PASSWORD_FILE", "")
    clear_settings_cache()
    yield tmp_path
    clear_settings_cache()


def _user_count() -> int:
    with connect() as connection:
        return connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]


def test_password_file_failure_rolls_back_the_account(empty_database, monkeypatch):
    import app.bootstrap_admin as bootstrap

    original_write = bootstrap.write_secret_file

    def refuse(target, text):
        raise PermissionError(13, "Permission denied", str(target))

    monkeypatch.setattr(bootstrap, "write_secret_file", refuse)
    with pytest.raises(desktop.InitialAdminError, match="不能写入初始密码文件"):
        desktop.initialize_admin(empty_database)
    assert _user_count() == 0
    assert not (empty_database / desktop.INITIAL_PASSWORD_FILE).exists()
    monkeypatch.setattr(bootstrap, "write_secret_file", original_write)
    desktop.initialize_admin(empty_database)
    assert _user_count() == 1
    assert (empty_database / desktop.INITIAL_PASSWORD_FILE).is_file()


def test_print_failure_rolls_back_and_removes_the_file(empty_database, monkeypatch):
    def broken_print(*_args, **_kwargs):
        raise OSError("console closed")

    monkeypatch.setattr("builtins.print", broken_print)
    with pytest.raises(OSError):
        desktop.initialize_admin(empty_database)
    assert _user_count() == 0
    assert not (empty_database / desktop.INITIAL_PASSWORD_FILE).exists()


def test_leftover_password_file_is_reported_not_overwritten(empty_database):
    leftover = empty_database / desktop.INITIAL_PASSWORD_FILE
    leftover.write_text("旧内容", encoding="utf-8")
    with pytest.raises(desktop.InitialAdminError, match="已存在"):
        desktop.initialize_admin(empty_database)
    assert _user_count() == 0
    assert leftover.read_text(encoding="utf-8") == "旧内容"


def test_offline_reset_gives_a_new_password_that_must_be_changed(empty_database, capsys):
    from app import reset_admin
    from app.identity import PASSWORD_CHANGE_GUARD_PREFIX, create_user

    desktop.initialize_admin(empty_database)
    create_user(account="writer", name="编写", password="writer-password-1", roles=["project_creator"])
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO sessions (id, user_id, token_hash, csrf_token, expires_at) "
                "SELECT 'session-1', id, 'hash', 'csrf', '2999-01-01T00:00:00+00:00' FROM users WHERE account = 'writer'"
            )
            connection.execute("INSERT INTO login_guards (account_key, failed_count) VALUES ('writer', 9)")
            connection.execute(
                "INSERT INTO login_guards (account_key, failed_count) "
                "SELECT ? || id, 9 FROM users WHERE account = 'writer'",
                (PASSWORD_CHANGE_GUARD_PREFIX,),
            )
    capsys.readouterr()
    assert reset_admin.main(["writer"]) == 0
    printed = capsys.readouterr().out
    password = next(line.split("：", 1)[1] for line in printed.splitlines() if "临时密码" in line)
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE account = 'writer'").fetchone()
        assert verify_password(password, row["password_hash"])
        assert not verify_password("writer-password-1", row["password_hash"])
        assert row["must_change_password"]
        assert connection.execute("SELECT revoked_at FROM sessions WHERE id = 'session-1'").fetchone()["revoked_at"]
        assert connection.execute("SELECT COUNT(*) AS total FROM login_guards").fetchone()["total"] == 0
    assert reset_admin.main(["nobody"]) == 1


def test_desktop_reset_writes_a_fresh_password_file(empty_database, capsys):
    desktop.initialize_admin(empty_database)
    target = empty_database / desktop.INITIAL_PASSWORD_FILE
    first = target.read_text(encoding="utf-8-sig")
    desktop.reset_admin(empty_database, "admin")
    second = target.read_text(encoding="utf-8-sig")
    assert first != second
    password = next(line.split("：", 1)[1] for line in second.splitlines() if line.startswith("初始密码"))
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE account = 'admin'").fetchone()
    assert verify_password(password, row["password_hash"]) and row["must_change_password"]


def test_initial_password_file_is_removed_after_first_change(empty_database, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app as api

    desktop.initialize_admin(empty_database)
    target = empty_database / desktop.INITIAL_PASSWORD_FILE
    password = next(line.split("：", 1)[1] for line in target.read_text(encoding="utf-8-sig").splitlines()
                    if line.startswith("初始密码"))
    other = empty_database / "其他文件.txt"
    other.write_text("账号：admin\n", encoding="utf-8")
    with TestClient(api) as client:
        csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
        signed = client.post("/api/v1/auth/login", json={"account": "admin", "password": password},
                             headers={"X-CSRF-Token": csrf})
        assert signed.status_code == 200, signed.text
        csrf = signed.json()["data"]["csrfToken"]
        wrong = client.post("/api/v1/auth/change-password",
                            json={"currentPassword": "wrong-password-0", "newPassword": "admin-chosen-pass-1"},
                            headers={"X-CSRF-Token": csrf})
        assert wrong.status_code == 422 and target.exists()
        changed = client.post("/api/v1/auth/change-password",
                              json={"currentPassword": password, "newPassword": "admin-chosen-pass-1"},
                              headers={"X-CSRF-Token": csrf})
        assert changed.status_code == 204, changed.text
    assert not target.exists()
    assert other.exists()


def test_desktop_reset_keeps_old_password_when_file_cannot_be_written(empty_database, monkeypatch, capsys):
    import app.bootstrap_admin as bootstrap

    desktop.initialize_admin(empty_database)
    target = empty_database / desktop.INITIAL_PASSWORD_FILE
    with connect() as connection:
        before = connection.execute("SELECT password_hash, revision FROM users WHERE account = 'admin'").fetchone()

    def refuse(path, text):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(bootstrap, "write_secret_file", refuse)
    capsys.readouterr()
    with pytest.raises(bootstrap.ResetError, match="密码没有改变"):
        desktop.reset_admin(empty_database, "admin")
    assert "临时密码" not in capsys.readouterr().out
    with connect() as connection:
        after = connection.execute("SELECT password_hash, revision FROM users WHERE account = 'admin'").fetchone()
    assert dict(after) == dict(before)
    assert not target.exists()


def _windows_user_sid() -> str:
    import csv
    import subprocess

    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    return next(csv.reader([out.strip()]))[1]


def _full_sid(sid: str) -> str:
    """CI（PR #1）：以内置 Administrator（RID 500）运行时 SDDL 里写的是别名 LA，不是完整 SID。
    比较前用 ConvertStringSidToSidW → ConvertSidToStringSidW 把别名（LA、BA、SY…）解析成完整 SID 字符串（ctypes，不依赖 pywin32）。"""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    binary = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid, ctypes.byref(binary)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(binary, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel32.LocalFree(binary)


def _icacls_dacl(path) -> tuple[bool, list[tuple[str, str, str]]]:
    """Independent of pywin32: ``icacls /save`` writes the SDDL (locale-independent)."""
    import re
    import subprocess

    saved = path.parent / (path.name + ".acl")
    subprocess.run(["icacls", str(path), "/save", str(saved)], capture_output=True, check=True)
    sddl = saved.read_bytes().decode("utf-16").splitlines()[1]
    saved.unlink()
    dacl = re.search(r"D:([A-Z]*)((?:\([^)]*\))*)", sddl)
    assert dacl, sddl
    aces = []
    for ace in re.findall(r"\(([^)]*)\)", dacl.group(2)):
        kind, flags, _rights, _object, _inherit_object, sid = ace.split(";")[:6]
        aces.append((kind, flags, sid))
    return "P" in dacl.group(1), aces


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL check")
def test_windows_acl_seen_by_icacls(tmp_path):
    from app.bootstrap_admin import write_secret_file

    secret = tmp_path / "secret.txt"
    write_secret_file(secret, "x")
    protected, aces = _icacls_dacl(secret)
    assert protected, aces
    assert len(aces) == 1, aces
    kind, flags, sid = aces[0]
    assert kind == "A" and "ID" not in flags
    assert _full_sid(sid) == _full_sid(_windows_user_sid()), (sid, _windows_user_sid())
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("x", encoding="utf-8")
    protected, aces = _icacls_dacl(ordinary)
    assert not protected or len(aces) != 1 or any("ID" in flags for _k, flags, _s in aces)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL check")
def test_windows_password_file_has_owner_only_protected_dacl(tmp_path):
    from app.bootstrap_admin import owner_only_acl_problem, write_secret_file

    secret = tmp_path / "secret.txt"
    write_secret_file(secret, "x")
    assert owner_only_acl_problem(secret) is None
    assert secret.read_text(encoding="utf-8-sig") == "x"
    # The checker is not vacuous: an ordinary file inherits the folder's ACEs.
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("x", encoding="utf-8")
    assert owner_only_acl_problem(ordinary) is not None
    with pytest.raises(FileExistsError):
        write_secret_file(secret, "y")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL check")
def test_windows_initial_admin_file_is_owner_only(empty_database):
    from app.bootstrap_admin import owner_only_acl_problem

    desktop.initialize_admin(empty_database)
    assert owner_only_acl_problem(empty_database / desktop.INITIAL_PASSWORD_FILE) is None


def test_desktop_reset_of_unknown_account_keeps_the_password_file(empty_database, monkeypatch, capsys):
    from app.bootstrap_admin import ResetError

    desktop.initialize_admin(empty_database)
    target = empty_database / desktop.INITIAL_PASSWORD_FILE
    original = target.read_bytes()
    with pytest.raises(ResetError, match="没有账号"):
        desktop.reset_admin(empty_database, "admln")
    assert target.read_bytes() == original
    monkeypatch.chdir(empty_database)
    monkeypatch.setattr(sys, "argv", ["文档工作室.exe", "--home", str(empty_database), "--reset-admin", "admln"])
    capsys.readouterr()
    assert desktop.main() == 1
    assert "没有账号 admln" in capsys.readouterr().err
    assert target.read_bytes() == original
