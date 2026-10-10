"""Create the first administrator.

There is no default password and no public registration. If any user
already exists, nothing is created. The first administrator must change the
initial password before any other API call is accepted.

Two ways:
- set BOOTSTRAP_ADMIN_ACCOUNT, BOOTSTRAP_ADMIN_NAME and BOOTSTRAP_ADMIN_PASSWORD
  (at least 12 characters); the server creates the account on start-up;
- run ``python -m app.bootstrap_admin`` without BOOTSTRAP_ADMIN_PASSWORD and a
  random initial password is generated and printed exactly once.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

from app.db import connect, migrate
from app.identity import MIN_PASSWORD_LENGTH, account_key, create_user, hash_password, verify_password

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
        "本文件对能访问此目录的本机用户可见，请勿复制或外传。\n"
    )


def write_secret_file(target: Path, text: str) -> None:
    """Create ``target`` exclusively, readable by the owner only, then write ``text``.

    The file never exists with wider permissions: it is created with mode 0600
    (POSIX). On Windows mode bits do not restrict access, so inheritance is
    removed with icacls and only the current user is granted access; if that
    fails the file is still written and a warning is logged.
    """
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        try:
            subprocess.run(
                ["icacls", str(target), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True, timeout=15, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            logger.warning("不能把初始密码文件限制为仅当前用户可读：%s", target)


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


# Earlier portable packages shipped this fixed password for "admin".
_KNOWN_DEFAULTS = (("admin", "admin123"),)


def flag_known_default_passwords() -> int:
    """Force a password change for accounts still using a published default."""
    flagged = 0
    with connect() as connection:
        with connection.transaction(immediate=True):
            for account, password in _KNOWN_DEFAULTS:
                row = connection.execute(
                    "select id, password_hash, must_change_password from users where account_key = %s",
                    (account,),
                ).fetchone()
                if row and not row["must_change_password"] and verify_password(password, row["password_hash"]):
                    connection.execute("update users set must_change_password = 1 where id = %s", (row["id"],))
                    flagged += 1
    return flagged


class ResetError(RuntimeError):
    """The offline reset could not be applied."""


def reset_account_password(account: str) -> str:
    """Offline recovery: give ``account`` a new random password that must be changed.

    Revokes every session of the account and clears its login lock-out.
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
            connection.execute("delete from login_guards where account_key = %s", (key,))
    return password


def main() -> None:
    migrate()
    if os.environ.get(_PASSWORD, ""):
        if bootstrap_first_admin():
            print("已创建第一个管理员。首次登录后必须修改密码。")
            return
        print("没有创建账号：已有用户，或环境变量不完整、密码少于 12 位。")
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
