"""Copy a verified SQLite/file snapshot into the portable Windows package."""

from __future__ import annotations

import argparse
from contextlib import closing
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
   仍在使用旧版固定初始密码的 admin 账号，登录后会被要求先修改密码。
   全新安装（没有任何账号）时，首次启动会生成随机初始密码，
   显示在启动窗口并保存到“初始管理员密码.txt”；登录改密后请删除该文件。
3. 使用结束后，可在启动窗口按 Ctrl+C 停止；再次双击即可启动。

无需安装 Python、Node.js 或 Docker。请勿只复制 exe：_internal 与 data 必须一起保留。
当前项目、Word 原稿、编辑内容、历史版本、AI 记忆、Skill、测试记录与截图均已迁移。
原电脑登录会话不迁移，请重新登录。账号以密码哈希形式迁移，原密码继续有效。

测试任务中保存的被测系统登录密码（testPassword 等凭据）不随包迁移，已在副本数据库中清除。
部署后请在“测试用例管理”中逐个任务重新填写被测系统密码，再执行测试。

部署包不包含 AI 密钥。需要 AI 编写时：
  把 .env.example 复制为 .env，在 AI_API_KEY= 后填写本机使用的密钥，然后重启程序。
  .env.example 已带上原环境的 AI 服务地址、模型和思考强度，数据库与文件目录为相对路径。

数据与配置：
  .env.example            配置模板（不含密钥）
  .env                    本机配置，自行创建，含 API Key，请勿外传
  data/app.db             SQLite 业务数据库
  data/files/             Word 文档、截图与其他业务文件
  logs/                   启动与服务日志

升级前关闭程序并备份 data 与 .env；更换程序文件时保留这两个位置。
不要同时运行开发项目与部署包，它们使用相同的 5173 端口。
移动到其他电脑后，测试任务中的前后端源码目录、被测服务地址、账号等需在页面重新确认。
已有测试截图与文档可继续查看；被测项目的源码、Python/Node 单元测试环境需自行准备。
需要 AI 编写时，电脑须能访问 .env 中的 AI 服务。
AI_REASONING_EFFORT 配置思考强度；留空使用模型默认值。可选 none、minimal、low、medium、high、xhigh、max，支持范围以模型服务为准。
修改 .env 后须关闭程序并重新启动。
精确 Word 版式预览需安装 Microsoft Word；Visio 原生生成需安装 Microsoft Visio。

部署校验.json 记录迁移时间、业务数量、清除的凭据数量和文件校验值，不包含任何密钥。
"""

# Never ship these from the source environment, whatever the caller passes.
SECRET_KEYS = frozenset({"AI_API_KEY", "BOOTSTRAP_ADMIN_PASSWORD"})


def env_example(values: dict[str, str]) -> str:
    """A configuration template carrying non-secret settings only."""
    lines = ["# 文档工作室配置模板：复制为 .env 后填写 AI_API_KEY，再重启程序。"]
    for key, value in values.items():
        if key in SECRET_KEYS:
            continue
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{key}='{escaped}'")
    lines.append("AI_API_KEY=''")
    return "\n".join(lines) + "\n"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tables(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def business_fingerprint(connection: sqlite3.Connection) -> str:
    result = hashlib.sha256()
    for table in tables(connection):
        if table in TRANSIENT_TABLES:
            continue
        result.update(table.encode())
        for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
            result.update(json.dumps(row, ensure_ascii=False, default=str).encode())
            result.update(b"\n")
    return result.hexdigest()


# JSON keys whose values are third-party credentials (e.g. test_tasks.config_json
# and test_task_runs.snapshot_json carry "testPassword" in clear text).
_SECRET_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)$")
_MASK = "******"


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


def scrub_credentials(connection: sqlite3.Connection) -> dict[str, int]:
    """Remove stored third-party credentials from the copied database.

    1. Every TEXT cell holding a JSON object/array loses keys that name a
       credential (testPassword, apiKey, token, ...).
    2. Any remaining occurrence of a removed value anywhere in a TEXT cell (a log
       that escaped redaction, for instance) is masked.
    Account password hashes in ``users.password_hash`` are kept: accounts must
    still be able to sign in after migration.
    """
    removed: list[str] = []
    counts = {"json_cells": 0, "masked_cells": 0}
    names = [table for table in tables(connection) if table not in TRANSIENT_TABLES]
    text_columns: dict[str, list[str]] = {}
    for table in names:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
                   if row[1] != "password_hash"]
        text_columns[table] = columns
        for column in columns:
            rows = connection.execute(
                f'SELECT rowid, "{column}" FROM "{table}" WHERE typeof("{column}")=\'text\' '
                f'AND (substr(ltrim("{column}"),1,1) IN (\'{{\',\'[\'))'
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
                    counts["json_cells"] += 1
    secrets = sorted({value for value in removed if len(value) >= 4}, key=len, reverse=True)
    if secrets:
        for table, columns in text_columns.items():
            for column in columns:
                for rowid, raw in connection.execute(
                        f'SELECT rowid, "{column}" FROM "{table}" WHERE typeof("{column}")=\'text\'').fetchall():
                    masked = raw
                    for secret in secrets:
                        masked = masked.replace(secret, _MASK)
                    if masked != raw:
                        connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (masked, rowid))
                        counts["masked_cells"] += 1
    counts["credentials_removed"] = len(removed)
    return counts


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
             env_updates: dict[str, str] | None = None) -> dict:
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
        target.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".snapshot-", dir=target) as temporary:
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
                scrubbed = scrub_credentials(copied)
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
            # The source .env holds secrets; ship a template instead of copying it.
            updates = {**(env_updates or {}), "DATABASE_URL": "sqlite:///./data/app.db", "FILE_DATA_DIR": "./data/files"}
            (stage / ".env.example").write_text(env_example(updates), encoding="utf-8")
            (stage / "使用说明.txt").write_text(README, encoding="utf-8-sig")
            metadata.update(database_bytes=destination.stat().st_size,
                            database_sha256=digest(destination), files_sha256=hashes)
            (stage / "部署校验.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            for name in ("data", ".env.example", "使用说明.txt", "部署校验.json"):
                (stage / name).replace(target / name)
        return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-server", type=Path, default=Path(__file__).resolve().parents[1] / "server")
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[1] / "release" / "文档工作室")
    parser.add_argument("--execute", action="store_true", help="copy; without this flag inspect only")
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
                          execute=args.execute, env_updates={
                              "AI_BASE_URL": settings.ai_base_url, "AI_MODEL": settings.ai_model,
                              "AI_CONTEXT_TOKENS": str(settings.ai_context_tokens),
                              "AI_REASONING_EFFORT": settings.ai_reasoning_effort,
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
