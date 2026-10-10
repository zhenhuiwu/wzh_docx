"""Offline password reset for a locked-out administrator.

Run on the server machine, with the same DATABASE_URL as the service::

    uv run python -m app.reset_admin            # resets "admin"
    uv run python -m app.reset_admin <account>

A new random password is printed once; the account must change it at the next
sign-in, and all of its sessions are revoked. The portable package exposes the
same action as ``文档工作室.exe --reset-admin [account]``.
"""

from __future__ import annotations

import argparse
import sys

from app.bootstrap_admin import ResetError, reset_account_password
from app.db import migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线重置管理员密码（生成随机密码，下次登录须修改）")
    parser.add_argument("account", nargs="?", default="admin", help="要重置的账号，默认 admin")
    args = parser.parse_args(argv)
    migrate()
    try:
        password = reset_account_password(args.account)
    except ResetError as error:
        print(f"没有重置：{error}", file=sys.stderr)
        return 1
    print(f"已重置账号 {args.account} 的密码，原有登录会话已全部失效。")
    print(f"新的临时密码（只显示这一次）：{password}")
    print("下次登录后必须修改密码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
