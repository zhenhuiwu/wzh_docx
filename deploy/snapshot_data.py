"""Copy a verified SQLite/file snapshot into the portable Windows package."""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory


TRANSIENT_TABLES = {
    "sessions", "csrf_challenges", "login_guards", "idempotency_keys",
    "writing_skill_requests",
}
README = """文档工作室 · 一键启动说明

1. 将整个文件夹解压到可写目录，双击“文档工作室.exe”。
2. 在浏览器打开 http://localhost:5173 ，用原有账号登录。
   已有账号和密码继续有效，包括 admin / admin123。
   全新安装（没有任何账号）时，首次启动会生成随机初始密码，
   保存到“初始管理员密码.txt”并显示在启动窗口；首次改密成功后该文件自动删除。
3. 使用结束后，可在启动窗口按 Ctrl+C 停止；再次双击即可启动。
4. 忘记管理员密码：先关闭程序，在本文件夹打开命令行执行
     文档工作室.exe --reset-admin
   （其他账号在后面加账号名），会生成新的临时密码并写入“初始管理员密码.txt”，登录后须修改。

无需安装 Python、Node.js 或 Docker。请勿只复制 exe：_internal 与 data 必须一起保留。
当前项目、Word 原稿、编辑内容、历史版本、AI 记忆、Skill、测试记录与截图均已迁移。
原电脑登录会话不迁移，请重新登录。账号以密码哈希形式迁移，原密码继续有效。

测试任务中保存的被测系统登录密码（testPassword 等凭据）不随包迁移，已在副本数据库中清除。
部署后请在“测试用例管理”中逐个任务重新填写被测系统密码，再执行测试。
打包时如果某个被测系统密码作为独立的词还出现在正文、账号名等其他地方，打包会停止并报出“表.列”；
确认是普通业务内容后用 build.ps1 -AllowCredentialText 表.列 放行，放行的表列和次数记在“部署校验.json”的 accepted_leftovers 中；
若确是泄露的密码，应先在原系统中删除该内容再打包。

build.ps1 生成的是私有迁移包：.env 已包含当前 AI 地址、模型、密钥和思考强度，可继续使用。
私有迁移包包含数据和 AI 密钥，请勿公开分享或提交到 Git。
单独使用快照工具默认不携带密钥；若包内只有 .env.example，将其复制为 .env 并填写 AI_API_KEY 后重启。
数据库与文件目录已改为包内相对路径；.env.example 始终不包含密钥。

数据与配置：
  .env.example            配置模板（不含密钥）
  .env                    私有迁移配置，含 API Key，请勿外传
  data/app.db             SQLite 业务数据库
  data/files/             Word 文档、截图与其他业务文件
  logs/                   启动与服务日志

升级前关闭程序并备份 data 与 .env；更换程序文件时保留这两个位置。
不要同时运行开发项目与部署包，它们使用相同的 5173 端口。
移动到其他电脑后，测试任务中的前后端源码目录、被测服务地址、账号等需在页面重新确认。
已有测试截图与文档可继续查看；被测项目的源码、Python/Node 单元测试环境需自行准备。
需要 AI 编写时，电脑须能访问 .env 中的 AI 服务。
AI_REASONING_EFFORT 配置思考强度；留空使用模型默认值。可选 none、minimal、low、medium、high、xhigh、max，支持范围以模型服务为准。
AI_READ_TIMEOUT_SECONDS 默认 600，表示单次模型请求无响应的等待秒数；不限制整份文档的总编写时长。
修改 .env 后须关闭程序并重新启动。
精确 Word 版式预览需安装 Microsoft Word；Visio 原生生成需安装 Microsoft Visio。

部署校验.json 记录迁移时间、业务数量、清除的凭据数量和文件校验值，不包含任何密钥。
"""

# The example never carries credentials; private migration only includes the AI key.
SECRET_KEYS = frozenset({"AI_API_KEY", "BOOTSTRAP_ADMIN_PASSWORD"})


def env_quote(value: str) -> str:
    r"""Quote ``value`` so python-dotenv (used by pydantic-settings) reads it back unchanged.

    Plain values use single quotes, where only \\ and \' are escapes. Values
    with line breaks or tabs use double quotes, where \n, \r, \t, \" and \\
    are decoded. python-dotenv expands ``${NAME}`` in every quoting style and
    offers no escape for it, so such values are refused instead of being
    silently changed.
    """
    if "${" in value:
        raise ValueError("配置值含有 ${，python-dotenv 会把它当作变量展开，无法原样写入 .env。")
    if not any(char in value for char in "\n\r\t"):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return '"' + escaped + '"'


def env_example(values: dict[str, str]) -> str:
    """A configuration template carrying non-secret settings only."""
    lines = ["# 文档工作室配置模板：复制为 .env 后填写 AI_API_KEY，再重启程序。"]
    for key, value in values.items():
        if key in SECRET_KEYS:
            continue
        lines.append(f"{key}={env_quote(str(value))}")
    lines.append("AI_API_KEY=''")
    return "\n".join(lines) + "\n"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tables(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def business_fingerprint(connection: sqlite3.Connection,
                         exclude: frozenset[tuple[str, str]] = frozenset()) -> str:
    """Digest of all non-transient rows; ``exclude`` drops (table, column) pairs."""
    result = hashlib.sha256()
    for table in tables(connection):
        if table in TRANSIENT_TABLES:
            continue
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
                   if (table, row[1]) not in exclude]
        selected = ", ".join(f'"{column}"' for column in columns)
        result.update(table.encode())
        for row in connection.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid'):
            result.update(json.dumps(row, ensure_ascii=False, default=str).encode())
            result.update(b"\n")
    return result.hexdigest()


# Columns that hold test-execution configuration and traces. Only these are
# changed: credential keys are removed from their JSON and leftover copies of the
# removed values are masked. Every other column must stay byte-for-byte equal.
SCRUB_COLUMNS: frozenset[tuple[str, str]] = frozenset({
    ("test_tasks", "config_json"),
    ("test_tasks", "cases_json"),
    ("test_tasks", "error"),
    ("test_task_runs", "snapshot_json"),
    ("test_task_runs", "result_json"),
})
# Columns that cannot carry a plaintext credential and would only produce
# accidental matches (hashes, digests, storage keys).
_OPAQUE_COLUMN = re.compile(r"(?i)(^id$|_id$|_hash$|sha256$|^storage_key$)")
_SECRET_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)$")
_MASK = "******"
_MIN_SECRET_LENGTH = 4


def _strip_secret_keys(value, found: list[str]):
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY.search(key):
                if isinstance(item, str) and item:
                    found.append(item)
                continue
            kept[key] = _strip_secret_keys(item, found)
        return kept
    if isinstance(value, list):
        return [_strip_secret_keys(item, found) for item in value]
    return value


def _stored_forms(secret: str) -> set[str]:
    """The secret as written, plus its JSON-escaped forms (ASCII and non-ASCII)."""
    return {secret, json.dumps(secret)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1]}


class CredentialLeftError(RuntimeError):
    """A removed credential still appears outside the test-execution columns."""


def scrub_credentials(connection: sqlite3.Connection,
                      allowed_leftovers: frozenset[str] = frozenset()) -> dict[str, object]:
    """Remove stored third-party credentials (test-system passwords) from the copy.

    1. In ``SCRUB_COLUMNS`` only, JSON keys that name a credential (testPassword,
       apiKey, token, ...) are removed.
    2. In ``SCRUB_COLUMNS`` only, remaining copies of the removed values (a log
       line that escaped redaction) are replaced by ``******``.
    3. Every other text column is searched for the removed values as a whole
       token. A hit is not replaced, because the value may be ordinary business
       text (a weak password such as 123456 or an account name); the migration
       stops and names the table and column so a person can decide. Columns the
       operator reviewed and listed in ``allowed_leftovers`` ("table.column")
       are shipped unchanged and their hit counts are recorded instead.
    4. The caller verifies that all columns outside ``SCRUB_COLUMNS`` are unchanged.
    """
    names = set(tables(connection))
    removed: list[str] = []
    keys_removed: dict[str, int] = {}
    masked: dict[str, int] = {}
    accepted: dict[str, int] = {}
    targets = sorted((table, column) for table, column in SCRUB_COLUMNS if table in names and column in
                     {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')})
    for table, column in targets:
        rows = connection.execute(
            f'SELECT rowid, "{column}" FROM "{table}" WHERE typeof("{column}")=\'text\' '
            f'AND substr(ltrim("{column}"),1,1) IN (\'{{\',\'[\')'
        ).fetchall()
        for rowid, raw in rows:
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            found: list[str] = []
            cleaned = _strip_secret_keys(parsed, found)
            if found:
                removed.extend(found)
                connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                                   (json.dumps(cleaned, ensure_ascii=False), rowid))
                keys_removed[f"{table}.{column}"] = keys_removed.get(f"{table}.{column}", 0) + len(found)
    # A secret with quotes, backslashes or non-ASCII characters is stored escaped
    # inside JSON columns (for example Ab\"c or \u5bc6), so match those forms too.
    secrets = sorted({form for value in removed if len(value) >= _MIN_SECRET_LENGTH
                      for form in _stored_forms(value)}, key=len, reverse=True)
    if secrets:
        for table, column in targets:
            for rowid, raw in connection.execute(
                    f'SELECT rowid, "{column}" FROM "{table}" WHERE typeof("{column}")=\'text\'').fetchall():
                text, hits = raw, 0
                for secret in secrets:
                    hits += text.count(secret)
                    text = text.replace(secret, _MASK)
                if hits:
                    connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (text, rowid))
                    masked[f"{table}.{column}"] = masked.get(f"{table}.{column}", 0) + hits
        pattern = re.compile("|".join(rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])" for secret in secrets))
        leftovers: dict[str, int] = {}
        for table in sorted(names - TRANSIENT_TABLES):
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall():
                column = row[1]
                if (table, column) in SCRUB_COLUMNS or _OPAQUE_COLUMN.search(column):
                    continue
                for (value,) in connection.execute(
                        f'SELECT "{column}" FROM "{table}" WHERE typeof("{column}")=\'text\'').fetchall():
                    hits = len(pattern.findall(value))
                    if hits:
                        leftovers[f"{table}.{column}"] = leftovers.get(f"{table}.{column}", 0) + hits
        accepted = {name: count for name, count in leftovers.items() if name in allowed_leftovers}
        leftovers = {name: count for name, count in leftovers.items() if name not in allowed_leftovers}
        if leftovers:
            where = "、".join(f"{name}（{count} 处）" for name, count in leftovers.items())
            raise CredentialLeftError(
                f"测试任务保存的被测系统密码还出现在 {where}。为避免误改业务数据，迁移已停止。"
                "请人工确认这些内容只是普通业务文本（例如编号或账号名恰好与弱密码相同），"
                "然后用 --allow-credential-text 表.列（build.ps1 用 -AllowCredentialText）重新打包；"
                "如果确实是泄露的密码，请先在原系统中删除或改写对应内容。"
            )
    return {
        "accepted_leftovers": accepted,
        "credentials_removed": len(removed),
        "keys_removed": keys_removed,
        "masked_occurrences": masked,
    }


def ensure_idle(connection: sqlite3.Connection) -> None:
    checks = {
        "test_tasks": "status IN ('running','analyzing')",
        "test_task_runs": "status='running'",
        "jobs": "status IN ('running','queued','pending')",
    }
    names = set(tables(connection))
    for table, condition in checks.items():
        if table in names and connection.execute(f'SELECT 1 FROM "{table}" WHERE {condition} LIMIT 1').fetchone():
            raise RuntimeError("仍有任务正在运行，请等待完成后重新迁移。")


def file_manifest(root: Path) -> dict[str, tuple[int, int]]:
    entries = {}
    for path in sorted(root.rglob("*")):
        if path.relative_to(root).parts[0] == "tmp":
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise RuntimeError("文件数据目录含外部链接，不能迁移。")
        if path.is_file():
            info = path.stat()
            entries[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns)
    return entries


def snapshot(database: Path, files: Path, env_file: Path, target: Path, *, execute: bool,
             env_updates: dict[str, str] | None = None,
             allowed_leftovers: frozenset[str] = frozenset(), include_ai_config: bool = False) -> dict:
    database, files, env_file, target = (path.resolve() for path in (database, files, env_file, target))
    if not database.is_file() or not files.is_dir() or not env_file.is_file():
        raise RuntimeError("数据库、文件数据目录或 .env 不存在。")
    if any(target.is_relative_to(source) for source in (database.parent, files)):
        raise RuntimeError("部署目录不能位于生产数据目录中。")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)) as source:
        ensure_idle(source)
        counts = {table: source.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                  for table in tables(source) if table not in TRANSIENT_TABLES}
        manifest = file_manifest(files)
        metadata = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database_bytes": database.stat().st_size,
            "file_count": len(manifest), "file_bytes": sum(size for size, _ in manifest.values()),
            "business_counts": counts, "cleared_transient_tables": sorted(TRANSIENT_TABLES),
        }
        if not execute:
            return metadata
        if any((target / name).exists() for name in ("data", ".env", ".env.example", "部署校验.json", "使用说明.txt")):
            raise RuntimeError("目标已含部署数据或配置，拒绝覆盖；请使用新的部署目录。")
        before, env_digest = business_fingerprint(source), digest(env_file)
        created = [path for path in (target, *target.parents) if not path.exists()]
        target.mkdir(parents=True, exist_ok=True)
        with remove_created_on_failure(created), TemporaryDirectory(prefix=".snapshot-", dir=target) as temporary:
            stage = Path(temporary)
            destination = stage / "data" / "app.db"
            destination.parent.mkdir()
            with closing(sqlite3.connect(destination)) as copied:
                source.backup(copied)
                if business_fingerprint(copied) != before:
                    raise RuntimeError("迁移期间业务数据发生变化，请重试。")
                required = copied.execute("SELECT storage_key,size_bytes,sha256 FROM files WHERE status='stored'").fetchall()
                copied_files = stage / "data" / "files"
                copied_files.mkdir()
                hashes = {}
                for relative in manifest:
                    original, output = files / relative, copied_files / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, output)
                    if (manifest[relative] != (original.stat().st_size, original.stat().st_mtime_ns)
                            or digest(original) != digest(output)):
                        raise RuntimeError("迁移期间文件发生变化，请重试。")
                    hashes[relative] = digest(output)
                for key, size, sha256 in required:
                    if not key or Path(key).name != key or "/" in key or "\\" in key:
                        raise RuntimeError("文件记录包含不合法的存储路径。")
                    output = copied_files / "objects" / key
                    if not output.is_file() or output.stat().st_size != size or (sha256 and digest(output) != sha256):
                        raise RuntimeError("存在缺失或损坏的业务文件，迁移已停止。")
                for table in TRANSIENT_TABLES & set(tables(copied)):
                    copied.execute(f'DELETE FROM "{table}"')
                if business_fingerprint(copied) != before:
                    raise RuntimeError("备份业务数据校验失败。")
                untouched = business_fingerprint(copied, SCRUB_COLUMNS)
                scrubbed = scrub_credentials(copied, allowed_leftovers)
                if business_fingerprint(copied, SCRUB_COLUMNS) != untouched:
                    raise RuntimeError("清除测试凭据时改动了测试执行记录以外的数据，迁移已停止。")
                orphan_roles = 0
                if {"user_roles", "users"}.issubset(tables(copied)):
                    orphan_roles = copied.execute(
                        "DELETE FROM user_roles WHERE NOT EXISTS (SELECT 1 FROM users WHERE users.id=user_roles.user_id)"
                    ).rowcount
                copied.commit()
                # Rebuild the file so deleted sessions and removed credentials do not
                # survive in free pages of the shipped database.
                copied.execute("VACUUM")
                if copied.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("备份数据库完整性检查失败。")
                if copied.execute("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError("备份数据库关联检查失败。")
                metadata.update(source_business_counts=counts.copy(), discarded_orphan_user_roles=orphan_roles,
                                scrubbed_credentials=scrubbed,
                                source_business_sha256=before, business_sha256=business_fingerprint(copied))
                metadata["business_counts"] = {table: copied.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                                               for table in tables(copied) if table not in TRANSIENT_TABLES}
            ensure_idle(source)
            if business_fingerprint(source) != before or file_manifest(files) != manifest or digest(env_file) != env_digest:
                raise RuntimeError("迁移期间数据或配置发生变化，请重试。")
            updates = {**(env_updates or {}), "DATABASE_URL": "sqlite:///./data/app.db", "FILE_DATA_DIR": "./data/files"}
            entries = ["data", ".env.example", "使用说明.txt", "部署校验.json"]
            if include_ai_config:
                from dotenv import dotenv_values

                keys = ("DATABASE_URL", "FILE_DATA_DIR", "FILE_MAX_BYTES", "AI_BASE_URL", "AI_MODEL",
                        "AI_CONTEXT_TOKENS", "AI_REASONING_EFFORT", "AI_READ_TIMEOUT_SECONDS", "AI_API_KEY")
                values = dotenv_values(env_file)
                updates = {**{key: values[key] for key in keys if key in values}, **updates}
                private_config = "# 私有迁移配置：包含 AI 密钥，请勿外传或提交到 Git。\n" + "".join(
                    f"{key}={env_quote(str(updates[key] or ''))}\n" for key in keys if key in updates)
                (stage / ".env").write_text(private_config, encoding="utf-8")
                entries.append(".env")
            (stage / ".env.example").write_text(env_example(updates), encoding="utf-8")
            (stage / "使用说明.txt").write_text(README, encoding="utf-8-sig")
            metadata.update(database_bytes=destination.stat().st_size,
                            database_sha256=digest(destination), files_sha256=hashes,
                            includes_ai_config=include_ai_config)
            (stage / "部署校验.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            for name in entries:
                (stage / name).replace(target / name)
        return metadata



@contextmanager
def remove_created_on_failure(created: list[Path]):
    """On failure remove the directories this run created (deepest first), only while empty."""
    try:
        yield
    except BaseException:
        for path in created:
            try:
                path.rmdir()
            except OSError:
                break
        raise

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-server", type=Path, default=Path(__file__).resolve().parents[1] / "server")
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[1] / "release" / "文档工作室")
    parser.add_argument("--execute", action="store_true", help="copy; without this flag inspect only")
    parser.add_argument("--include-ai-config", action="store_true", help="私有迁移：携带当前 AI 配置和密钥，请勿外传")
    parser.add_argument("--allow-credential-text", action="append", default=[], metavar="TABLE.COLUMN",
                        help="经人工确认后，允许该表列中出现与测试密码相同的独立词（原样迁移并记录次数）")
    args = parser.parse_args()
    server = args.source_server.resolve()
    sys.path.insert(0, str(server))
    try:
        from app.config import Settings
        settings = Settings(_env_file=server / ".env")
        if not settings.database_url.startswith("sqlite:///"):
            raise RuntimeError("一键部署包只支持 SQLite。")
        result = snapshot(server / settings.database_url.removeprefix("sqlite:///"),
                          server / settings.file_data_dir, server / ".env", args.target,
                          execute=args.execute, allowed_leftovers=frozenset(args.allow_credential_text),
                          include_ai_config=args.include_ai_config,
                          env_updates={
                              "AI_BASE_URL": settings.ai_base_url, "AI_MODEL": settings.ai_model,
                              "AI_API_KEY": settings.ai_api_key,
                              "AI_CONTEXT_TOKENS": str(settings.ai_context_tokens),
                              "AI_REASONING_EFFORT": settings.ai_reasoning_effort,
                              "AI_READ_TIMEOUT_SECONDS": str(settings.ai_read_timeout_seconds),
                              "FILE_MAX_BYTES": str(settings.file_max_bytes),
                          })
        result.pop("files_sha256", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except RuntimeError as error:
        print(f"迁移失败：{error}", file=sys.stderr)
    except Exception as error:
        print(f"迁移失败（{type(error).__name__}）；未输出配置或密钥，请检查源目录与目标权限。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
