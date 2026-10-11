"""Create the first administrator.

There is no default password and no public registration. If any user
already exists, nothing is created. The first administrator must change the
initial password before any other API call is accepted.

Two ways:
- set BOOTSTRAP_ADMIN_ACCOUNT, BOOTSTRAP_ADMIN_NAME and BOOTSTRAP_ADMIN_PASSWORD
  (at least 8 characters); the server creates the account on start-up;
- run ``python -m app.bootstrap_admin`` without BOOTSTRAP_ADMIN_PASSWORD and a
  random initial password is generated and printed exactly once.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path

from app.db import connect, migrate
from app.identity import (
    MIN_PASSWORD_LENGTH,
    account_key,
    create_user,
    hash_password,
    password_change_guard,
    verify_password,
)

_ACCOUNT = "BOOTSTRAP_ADMIN_ACCOUNT"
_NAME = "BOOTSTRAP_ADMIN_NAME"
_PASSWORD = "BOOTSTRAP_ADMIN_PASSWORD"  # noqa: S105 - environment variable name, not a secret

logger = logging.getLogger(__name__)

INITIAL_PASSWORD_FILE = "初始管理员密码.txt"
# Set by the portable entry so a successful first password change can remove the file.
INITIAL_PASSWORD_FILE_ENV = "DOCWB_INITIAL_PASSWORD_FILE"


def initial_password_text(account: str, password: str) -> str:
    return (
        "文档工作室初始管理员\n"
        f"账号：{account}\n"
        f"初始密码：{password}\n"
        "首次登录后系统会要求修改密码，修改成功后本文件会被自动删除。\n"
        "本文件仅限当前系统用户读取，请勿复制或外传。\n"
    )


def write_secret_file(target: Path, text: str) -> None:
    """Create ``target`` exclusively, readable by the current user only, then write ``text``.

    The file never exists with wider permissions:
    - POSIX: created with mode 0600;
    - Windows: created with a protected DACL holding one ACE for the current
      process user's SID (nothing inherited from the folder), then read back.
    Any failure raises ``OSError`` (``FileExistsError`` if the file exists) and
    leaves no file behind, so the caller can roll back.
    """
    data = text.encode("utf-8-sig")
    if sys.platform == "win32":
        _write_owner_only_windows(target, data)
        return
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _current_user_sid():
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        win32api.CloseHandle(token)


def owner_only_acl_problem(target: Path) -> str | None:
    """Windows: describe why ``target`` is not owner-only, or None if it is."""
    import win32security

    sid = _current_user_sid()
    descriptor = win32security.GetFileSecurity(str(target), win32security.DACL_SECURITY_INFORMATION)
    control, _revision = descriptor.GetSecurityDescriptorControl()
    if not control & win32security.SE_DACL_PROTECTED:
        return "DACL 未受保护，仍会继承目录权限"
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:
        return "没有 DACL（所有人可访问）"
    if dacl.GetAceCount() != 1:
        return f"DACL 有 {dacl.GetAceCount()} 条 ACE，应只有当前用户 1 条"
    (ace_type, ace_flags), _mask, ace_sid = dacl.GetAce(0)
    if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE or ace_flags & 0x10 or ace_sid != sid:
        return "唯一的 ACE 不是授予当前用户的非继承允许项"
    return None


def _write_owner_only_windows(target: Path, data: bytes) -> None:
    try:
        import ntsecuritycon
        import pywintypes
        import win32file
        import win32security
    except ImportError as error:
        raise OSError(f"缺少 pywin32，不能创建仅当前用户可读的文件：{error}") from None

    try:
        sid = _current_user_sid()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid)
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorOwner(sid, False)
        descriptor.SetSecurityDescriptorDacl(True, dacl, False)
        descriptor.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        handle = win32file.CreateFile(
            str(target), win32file.GENERIC_WRITE, 0, attributes,
            win32file.CREATE_NEW, win32file.FILE_ATTRIBUTE_NORMAL, None,
        )
    except pywintypes.error as error:
        if error.winerror in (80, 183):  # ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS
            raise FileExistsError(17, "文件已存在", str(target)) from None
        raise OSError(f"不能创建仅当前用户可读的文件：{error.strerror}") from None
    try:
        try:
            win32file.WriteFile(handle, data)
            win32file.FlushFileBuffers(handle)
        finally:
            handle.Close()
        problem = owner_only_acl_problem(target)
        if problem:
            raise OSError(f"初始密码文件权限校验失败：{problem}")
    except BaseException as error:
        target.unlink(missing_ok=True)
        if isinstance(error, pywintypes.error):
            raise OSError(f"不能写入仅当前用户可读的文件：{error.strerror}") from None
        raise


def remove_initial_password_file(account: str) -> bool:
    """Delete the portable initial password file once ``account`` changed its password."""
    configured = os.environ.get(INITIAL_PASSWORD_FILE_ENV, "").strip()
    if not configured:
        return False
    target = Path(configured)
    try:
        lines = target.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("不能读取初始密码文件：%s", target, exc_info=True)
        return False
    if f"账号：{account}" not in lines:
        return False
    try:
        target.unlink()
    except OSError:
        logger.warning("改密成功，但不能删除初始密码文件：%s", target, exc_info=True)
        return False
    logger.info("初始密码已修改，已删除 %s", target)
    return True


def generate_initial_password() -> str:
    """A random one-time password that satisfies the minimum length."""
    return secrets.token_urlsafe(15)


def create_initial_admin(account: str, name: str, password: str, *, connection=None) -> None:
    create_user(
        account=account,
        name=name,
        password=password,
        roles=["system_admin"],
        connection=connection,
        must_change_password=True,
    )


def _has_users() -> bool:
    with connect() as connection:
        existing = connection.execute("select count(*) as total from users").fetchone()
    return bool(existing and int(existing["total"]) > 0)


def bootstrap_first_admin() -> bool:
    account = os.environ.get(_ACCOUNT, "").strip()
    name = os.environ.get(_NAME, "").strip()
    password = os.environ.get(_PASSWORD, "")
    if not account or not name or not password:
        return False
    if len(password) < MIN_PASSWORD_LENGTH:
        return False
    if _has_users():
        return False
    create_initial_admin(account, name, password)
    return True


class ResetError(RuntimeError):
    """The offline reset could not be applied."""


def reset_account_password(account: str, before_commit=None) -> str:
    """Offline recovery: give ``account`` a new random password that must be changed.

    Revokes every session of the account and clears its sign-in and
    change-password lock-outs. ``before_commit(password)`` runs inside the
    transaction (e.g. to write the password file); if it raises, nothing changes.
    Returns the new password; the caller shows it to the operator once.
    """
    key = account_key(account)
    password = generate_initial_password()
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute("select id from users where account_key = %s", (key,)).fetchone()
            if row is None:
                raise ResetError(f"没有账号 {account}。")
            connection.execute(
                """
                update users
                set password_hash = %s, must_change_password = 1,
                    revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (hash_password(password), row["id"]),
            )
            connection.execute(
                "update sessions set revoked_at = now() where user_id = %s and revoked_at is null",
                (row["id"],),
            )
            connection.execute(
                "delete from login_guards where account_key in (%s, %s)",
                (key, password_change_guard(row["id"])),
            )
            if before_commit is not None:
                before_commit(password)
    return password


def main() -> None:
    migrate()
    if os.environ.get(_PASSWORD, ""):
        if bootstrap_first_admin():
            print("已创建第一个管理员。首次登录后必须修改密码。")
            return
        print(f"没有创建账号：已有用户，或环境变量不完整、密码少于 {MIN_PASSWORD_LENGTH} 位。")
        raise SystemExit(1)
    if _has_users():
        print("没有创建账号：已有用户。")
        raise SystemExit(1)
    account = os.environ.get(_ACCOUNT, "").strip() or "admin"
    name = os.environ.get(_NAME, "").strip() or "管理员"
    password = generate_initial_password()
    create_initial_admin(account, name, password)
    print(f"已创建第一个管理员：账号 {account}")
    print(f"初始密码（只显示这一次）：{password}")
    print("首次登录后必须修改密码。")


if __name__ == "__main__":
    sys.exit(main())
