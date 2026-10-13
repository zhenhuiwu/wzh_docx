"""回滚测试只关心“到某号为止”的库：把迁移目录限制在 <= LAST，后续新增迁移（119 起）不再让旧回滚测试失效。"""

import shutil

import pytest

from app import db

LAST = 118


@pytest.fixture(autouse=True)
def cap_migrations(tmp_path, monkeypatch):
    source = db.migrations_dir()
    capped = tmp_path / f"migrations_upto_{LAST}"
    capped.mkdir()
    for path in source.glob("*.sql"):
        if int(path.stem.split("_", 1)[0]) <= LAST:
            shutil.copy(path, capped / path.name)
    monkeypatch.setattr(db, "migrations_dir", lambda: capped)
    yield
