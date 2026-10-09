"""Create the first administrator from the process environment.

There is no default password and no public registration. If any user
already exists, the environment values are ignored.
"""

from __future__ import annotations

import os
import sys

from app.db import connect, migrate
from app.identity import MIN_PASSWORD_LENGTH, create_user

_ACCOUNT = "BOOTSTRAP_ADMIN_ACCOUNT"
_NAME = "BOOTSTRAP_ADMIN_NAME"
_PASSWORD = "BOOTSTRAP_ADMIN_PASSWORD"


def bootstrap_first_admin() -> bool:
    account = os.environ.get(_ACCOUNT, "").strip()
    name = os.environ.get(_NAME, "").strip()
    password = os.environ.get(_PASSWORD, "")
    if not account or not name or not password:
        return False
    if len(password) < MIN_PASSWORD_LENGTH:
        return False
    with connect() as connection:
        existing = connection.execute("select count(*) as total from users").fetchone()
    if existing and int(existing["total"]) > 0:
        return False
    create_user(
        account=account,
        name=name,
        password=password,
        roles=["system_admin"],
    )
    return True


def main() -> None:
    migrate()
    if bootstrap_first_admin():
        print("已创建第一个管理员。")
        return
    print("没有创建账号。")
    raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
