"""S1：迁移 110 只在两种情况下补 owner（01 §3.3、用例 30）。"""

from uuid import uuid4

from app.db import connect, migrate
from app.identity import create_user


def _roles(connection, project):
    rows = connection.execute("SELECT user_id, role FROM project_members WHERE project_id=%s", (project,)).fetchall()
    return {row["user_id"]: row["role"] for row in rows}


def test_owner_backfill_only_for_missing_or_ownerless_projects():
    assert "110_project_member_owner_backfill.sql" in migrate()
    with connect() as connection:
        alice = create_user(account="m110-a", name="甲", password="migration-110-pw", roles=["project_creator"], connection=connection)
        bob = create_user(account="m110-b", name="乙", password="migration-110-pw", roles=["project_creator"], connection=connection)
        legacy, ownerless, demoted, removed = (uuid4() for _ in range(4))
        for pid in (legacy, ownerless, demoted, removed):
            connection.execute("INSERT INTO projects(id,name,owner_id) VALUES(%s,%s,%s)", (pid, str(pid), alice))
        # 旧项目：没有成员行
        # 没有 owner：owner_id 用户是 editor
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'editor')", (ownerless, alice))
        # 有意降级：另有 owner，owner_id 用户是 editor
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'owner')", (demoted, bob))
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'editor')", (demoted, alice))
        # 有意移出：只剩 bob 为 owner
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'owner')", (removed, bob))
        connection.execute("DELETE FROM schema_migrations WHERE version='110_project_member_owner_backfill'")
        connection.commit()
    assert migrate() == ["110_project_member_owner_backfill.sql"]
    with connect() as connection:
        assert _roles(connection, legacy) == {alice: "owner"}
        assert _roles(connection, ownerless) == {alice: "owner"}
        assert _roles(connection, demoted) == {bob: "owner", alice: "editor"}
        assert _roles(connection, removed) == {bob: "owner"}
    assert migrate() == []
