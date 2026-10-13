"""迁移回滚链的唯一登记处（测试共用）。

新增一条带单独回滚脚本的迁移时，只需在 CHAIN 末尾追加一行 (编号, 迁移文件名不含 .sql)，
并提供 app/rollback_migration_<编号>.py（导出 rollback(path, apply, force)）。
奇偶占号见 wzh_collab/migrations-占号.md（任务与模板用偶数，基线与工作台用奇数）。
"""

from __future__ import annotations

import importlib
from pathlib import Path

CHAIN: list[tuple[int, str]] = [
    (114, "114_document_objects_deleted_after"),
    (115, "115_document_conflict_mine_snapshots"),
    (116, "116_project_members_v2"),
    (117, "117_template_versions"),
    (118, "118_document_assignments"),
    (119, "119_document_baselines"),
    (120, "120_tasks_templates"),
    (122, "122_upstream_baseline_ack"),
]


def files_after(number: int) -> list[str]:
    """编号大于 number 的登记迁移文件名（migrate() 按此顺序返回）。"""
    return [f"{name}.sql" for n, name in CHAIN if n > number]


def files_from(number: int) -> list[str]:
    return files_after(number - 1)


def _rollback(number: int):
    return importlib.import_module(f"app.rollback_migration_{number}").rollback


def rollback_newer_than(path: Path, number: int, apply: bool = True, force: bool = False) -> int:
    """从新到旧回滚所有编号大于 number 的迁移；返回第一个非零退出码，全部成功返回 0。"""
    for n, _name in reversed(CHAIN):
        if n <= number:
            break
        code = _rollback(n)(path, apply=apply, force=force)
        if code:
            return code
    return 0
