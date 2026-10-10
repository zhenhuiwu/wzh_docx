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

import os
import secrets
import sys

from app.db import connect, migrate
from app.identity import MIN_PASSWORD_LENGTH, create_user, verify_password

_ACCOUNT = "BOOTSTRAP_ADMIN_ACCOUNT"
_NAME = "BOOTSTRAP_ADMIN_NAME"
_PASSWORD = "BOOTSTRAP_ADMIN_PASSWORD"  # noqa: S105 - environment variable name, not a secret


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
