"""Server sessions, CSRF, and the auth routes.

Browser storage is not a credential. Passwords are verified here and are
never written to logs or response bodies.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from contextlib import nullcontext
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from app.db import connect
from app.errors import AppError
from app.models import format_timestamp
from app.permissions import GLOBAL_ROLES, has_permission, permissions_for

router = APIRouter()


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    password: str


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currentPassword: str
    newPassword: str


def _matches(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)

MIN_PASSWORD_LENGTH = 12
_ITERATIONS = 210_000
_SESSION_HOURS = 12
_CHALLENGE_MINUTES = 10
_FAILURE_LIMIT = 8
_FAILURE_WINDOW = timedelta(minutes=10)
_SESSION_COOKIE = "docwb_session"
_CHALLENGE_COOKIE = "docwb_csrf"
_DUMMY_HASH = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token() -> str:
    return secrets.token_urlsafe(32)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def account_key(account: str) -> str:
    return account.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    )
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds_text, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(rounds_text),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password("dummy-password-value")
    return _DUMMY_HASH


def _clean_account(account: str) -> str:
    cleaned = account.strip()
    if not cleaned or len(cleaned) > 64 or any(ord(char) < 32 for char in cleaned):
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "工号不正确。",
            [{"field": "account", "message": "请填写有效工号。"}],
        )
    return cleaned


def _clean_password(password: str, field: str) -> str:
    if not password or len(password) > 1024:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "密码不正确。",
            [{"field": field, "message": "请填写密码。"}],
        )
    return password


def _require_new_password(password: str) -> str:
    cleaned = _clean_password(password, "newPassword")
    if len(cleaned) < MIN_PASSWORD_LENGTH:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "新密码太短。",
            [{"field": "newPassword", "message": "至少需要 12 位。"}],
        )
    return cleaned


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    account: str
    name: str
    department: str
    roles: tuple[str, ...]
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
    session_id: UUID
    csrf_token: str
    expires_at: datetime

    def public_user(self) -> dict[str, object]:
        return {
            "id": str(self.user_id),
            "account": self.account,
            "name": self.name,
            "department": self.department,
            "globalRoles": list(self.roles),
            "status": self.status,
            "revision": self.revision,
            "createdAt": format_timestamp(self.created_at),
            "updatedAt": format_timestamp(self.updated_at),
            "lastLoginAt": (
                None
                if self.last_login_at is None
                else format_timestamp(self.last_login_at)
            ),
        }

    def public_session(self, request: Request) -> dict[str, object]:
        return {
            "data": {
                "user": self.public_user(),
                "permissions": permissions_for(self.roles),
                "expiresAt": format_timestamp(self.expires_at),
                "csrfToken": self.csrf_token,
            },
            "requestId": request.state.request_id,
        }


def create_user(
    *,
    account: str,
    name: str,
    password: str,
    roles: list[str],
    department: str = "",
    connection=None,
) -> UUID:
    cleaned = _clean_account(account)
    display_name = name.strip()
    if not display_name or len(display_name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "姓名不正确。")
    if len(department) > 120:
        raise AppError(422, "VALIDATION_FAILED", "部门不正确。")
    _require_new_password(password)
    unknown = [role for role in roles if role not in GLOBAL_ROLES]
    if not roles or unknown:
        raise AppError(422, "VALIDATION_FAILED", "角色不正确。")
    user_id = uuid4()
    password_hash = hash_password(password)
    own_connection = connection is None
    with (connect() if own_connection else nullcontext(connection)) as connection:
        with (connection.transaction(immediate=True) if own_connection else nullcontext()):
            conflict = connection.execute(
                "select id from users where account_key = %s",
                (account_key(cleaned),),
            ).fetchone()
            if conflict:
                raise AppError(409, "DUPLICATE_NAME", "这个工号已经存在。")
            connection.execute(
                """
                insert into users (
                  id, account, account_key, name, department,
                  password_hash, status
                )
                values (%s, %s, %s, %s, %s, %s, 'active')
                """,
                (
                    user_id,
                    cleaned,
                    account_key(cleaned),
                    display_name,
                    department,
                    password_hash,
                ),
            )
            for role in roles:
                connection.execute(
                    "insert into user_roles (user_id, role) values (%s, %s)",
                    (user_id, role),
                )
    return user_id


def _cookie_kwargs(max_age: int) -> dict[str, object]:
    return {
        "httponly": True,
        "secure": os.environ.get("COOKIE_SECURE", "").strip() == "1",
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
    }


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        **_cookie_kwargs(_SESSION_HOURS * 3600),
    )


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/")


def _load_principal(token: str) -> Principal | None:
    if not token:
        return None
    with connect() as connection:
        row = connection.execute(
            """
            select s.id as session_id, s.csrf_token, s.expires_at, s.revoked_at,
                   u.id as user_id, u.account, u.name, u.department, u.status,
                   u.revision, u.created_at, u.updated_at, u.last_login_at
            from sessions s
            join users u on u.id = s.user_id
            where s.token_hash = %s
            """,
            (_digest(token),),
        ).fetchone()
        if row is None:
            return None
        roles = connection.execute(
            """
            select role from user_roles
            where user_id = %s
            order by role
            """,
            (row["user_id"],),
        ).fetchall()
    if row["revoked_at"] is not None or row["expires_at"] <= _now():
        return None
    if row["status"] != "active":
        return None
    return Principal(
        user_id=row["user_id"],
        account=row["account"],
        name=row["name"],
        department=row["department"],
        roles=tuple(item["role"] for item in roles),
        status=row["status"],
        revision=row["revision"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
        session_id=row["session_id"],
        csrf_token=row["csrf_token"],
        expires_at=row["expires_at"],
    )


def current_principal(request: Request) -> Principal | None:
    return _load_principal(request.cookies.get(_SESSION_COOKIE, ""))


def require_session(request: Request) -> Principal:
    principal = current_principal(request)
    if principal is None:
        raise AppError(401, "AUTH_REQUIRED", "需要登录后才能继续。")
    return principal


def require_csrf(request: Request, principal: Principal) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied or not _matches(supplied, principal.csrf_token):
        raise AppError(403, "FORBIDDEN", "请求校验没有通过。")


def require_permission(request: Request, permission: str) -> Principal:
    principal = require_session(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_csrf(request, principal)
    if not has_permission(principal.roles, permission):
        raise AppError(403, "FORBIDDEN", "没有执行这个操作的权限。")
    return principal


def _locked(connection, key: str) -> bool:
    row = connection.execute(
        "select failed_count, window_started_at from login_guards where account_key = %s",
        (key,),
    ).fetchone()
    if row is None:
        return False
    if _now() - row["window_started_at"] > _FAILURE_WINDOW:
        return False
    return int(row["failed_count"]) >= _FAILURE_LIMIT


def _record_failure(key: str) -> None:
    with connect() as connection:
        with connection.transaction():
            row = connection.execute(
                """
                select failed_count, window_started_at
                from login_guards
                where account_key = %s
                """,
                (key,),
            ).fetchone()
            if row is None or _now() - row["window_started_at"] > _FAILURE_WINDOW:
                connection.execute(
                    """
                    insert into login_guards (account_key, failed_count, window_started_at)
                    values (%s, 1, now())
                    on conflict (account_key) do update
                    set failed_count = 1, window_started_at = now()
                    """,
                    (key,),
                )
                return
            connection.execute(
                """
                update login_guards
                set failed_count = failed_count + 1
                where account_key = %s
                """,
                (key,),
            )


def _clear_failures(connection, key: str) -> None:
    connection.execute("delete from login_guards where account_key = %s", (key,))


def _consume_challenge(request: Request) -> None:
    challenge_id = request.cookies.get(_CHALLENGE_COOKIE, "")
    supplied = request.headers.get("x-csrf-token", "")
    if not challenge_id or not supplied:
        raise AppError(403, "FORBIDDEN", "请求校验没有通过。")
    try:
        parsed = UUID(challenge_id)
    except ValueError:
        raise AppError(403, "FORBIDDEN", "请求校验没有通过。") from None
    with connect() as connection:
        with connection.transaction():
            row = connection.execute(
                """
                select token_hash, expires_at, used_at
                from csrf_challenges
                where id = %s
                """,
                (parsed,),
            ).fetchone()
            if (
                row is None
                or row["used_at"] is not None
                or row["expires_at"] <= _now()
                or not _matches(row["token_hash"], _digest(supplied))
            ):
                raise AppError(403, "FORBIDDEN", "请求校验没有通过。")
            connection.execute(
                "update csrf_challenges set used_at = now() where id = %s",
                (parsed,),
            )


def _issue_session(user_id: UUID) -> tuple[str, datetime, str]:
    token = _token()
    csrf_token = _token()
    expires_at = _now() + timedelta(hours=_SESSION_HOURS)
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                """
                insert into sessions (id, user_id, token_hash, csrf_token, expires_at)
                values (%s, %s, %s, %s, %s)
                """,
                (uuid4(), user_id, _digest(token), csrf_token, expires_at),
            )
            connection.execute(
                """
                update users
                set last_login_at = now(), updated_at = now()
                where id = %s
                """,
                (user_id,),
            )
    return token, expires_at, csrf_token


@router.get("/auth/csrf")
def get_csrf(request: Request, response: Response) -> dict[str, object]:
    # 简化版本：直接生成 CSRF token，不检查现有 session
    # 这样未登录用户也可以获取 token
    challenge_id = uuid4()
    token = _token()

    try:
        with connect() as connection:
            connection.execute(
                """
                insert into csrf_challenges (id, token_hash, expires_at)
                values (?, ?, ?)
                """,
                (
                    str(challenge_id),
                    _digest(token),
                    (_now() + timedelta(minutes=_CHALLENGE_MINUTES)).isoformat(),
                ),
            )
            connection.commit()
    except Exception as e:
        # 记录错误但不抛出，这样至少可以看到问题
        print(f"CSRF insertion error: {e}")
        import traceback
        traceback.print_exc()
        raise

    response.set_cookie(
        _CHALLENGE_COOKIE,
        str(challenge_id),
        **_cookie_kwargs(_CHALLENGE_MINUTES * 60),
    )
    return {
        "data": {"csrfToken": token},
        "requestId": request.state.request_id,
    }


@router.post("/auth/login")
def login(request: Request, response: Response, body: LoginBody) -> dict[str, object]:
    cleaned = _clean_account(body.account)
    secret = _clean_password(body.password, "password")
    _consume_challenge(request)
    key = account_key(cleaned)
    with connect() as connection:
        if _locked(connection, key):
            raise AppError(429, "LIMIT_EXCEEDED", "尝试次数过多，请稍后再登录。")
        row = connection.execute(
            """
            select id, password_hash, status
            from users
            where account_key = %s
            """,
            (key,),
        ).fetchone()
    stored = row["password_hash"] if row else _dummy_hash()
    matched = verify_password(secret, stored)
    if row is None or not matched:
        _record_failure(key)
        raise AppError(401, "AUTH_REQUIRED", "工号或密码不正确。")
    if row["status"] != "active":
        raise AppError(403, "FORBIDDEN", "账号已停用。")
    token, _expires_at, _csrf = _issue_session(row["id"])
    with connect() as connection:
        with connection.transaction():
            _clear_failures(connection, key)
    principal = _load_principal(token)
    if principal is None:
        raise AppError(500, "INTERNAL_ERROR", "服务暂时不能完成这个请求。")
    _set_session_cookie(response, token)
    _clear_cookie(response, _CHALLENGE_COOKIE)
    return principal.public_session(request)


@router.get("/auth/me")
def get_current_session(request: Request) -> dict[str, object]:
    return require_session(request).public_session(request)


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    principal = require_session(request)
    require_csrf(request, principal)
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                """
                update sessions
                set revoked_at = now()
                where id = %s and revoked_at is null
                """,
                (principal.session_id,),
            )
    result = Response(status_code=204)
    _clear_cookie(result, _SESSION_COOKIE)
    return result


@router.post("/auth/change-password", status_code=204)
def change_password(request: Request, body: PasswordBody) -> Response:
    principal = require_session(request)
    require_csrf(request, principal)
    current = _clean_password(body.currentPassword, "currentPassword")
    new_password = _require_new_password(body.newPassword)
    with connect() as connection:
        row = connection.execute(
            "select password_hash from users where id = %s",
            (principal.user_id,),
        ).fetchone()
    if row is None or not verify_password(current, row["password_hash"]):
        raise AppError(401, "AUTH_REQUIRED", "当前密码不正确。")
    if current == new_password:
        raise AppError(422, "VALIDATION_FAILED", "新密码需要和当前密码不同。")
    updated = hash_password(new_password)
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                """
                update users
                set password_hash = %s, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (updated, principal.user_id),
            )
            connection.execute(
                """
                update sessions
                set revoked_at = now()
                where user_id = %s and id <> %s and revoked_at is null
                """,
                (principal.user_id, principal.session_id),
            )
            connection.execute(
                """
                update sessions
                set csrf_token = %s
                where id = %s
                """,
                (_token(), principal.session_id),
            )
    return Response(status_code=204)
