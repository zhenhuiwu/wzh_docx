"""Memory survives reconnects without mixing documents or losing original requests."""

from uuid import uuid4
from contextlib import contextmanager

import pytest
from tests.block_ops_helper import section_answer

from app.config import clear_settings_cache
from app.db import connect, migrate
from app.document_memory import (MEMORY_MAX_CHARS, SUMMARY_ROW_LIMIT, TASK_EARLY_ROW_LIMIT,
                                 TASK_RECENT_ROW_LIMIT, clear_memory, document_context,
                                 load_memory, load_task_memory, read_memory_history, record_memory, section_memory_targets)
from app.identity import create_user


def test_accepted_memory_is_durable_bounded_and_document_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'memory.db'}")
    clear_settings_cache()
    try:
        migrate()
        owner = create_user(account="memory-test", name="测试", password="memory-password", roles=["system_admin"])
        project, first, second = uuid4(), uuid4(), uuid4()
        with connect() as conn:
            conn.execute("INSERT INTO projects (id, name, owner_id) VALUES (%s, %s, %s)", (project, "记忆测试", owner))
            for doc in (first, second):
                conn.execute("INSERT INTO documents (id, project_id, name, owner_id) VALUES (%s, %s, %s, %s)",
                             (doc, project, str(doc), owner))
            conn.commit()
        assert load_memory(first) == ""  # Draft generation must not create accepted memory.
        with connect() as conn:
            for revision in range(1, 41):
                record_memory(conn, first, f"要求 {revision}：" + "原始细节" * 200, "section", "范围", revision)
            record_memory(conn, first, "禁止重复写入", "section", "范围", 40)
            record_memory(conn, first, "要求 40：" + "原始细节" * 200, "section", "范围", 41)
            record_memory(conn, first, "", "section", "范围", 42)
            conn.commit()
        memory = load_memory(first)
        assert "要求 40" in memory and len(memory) <= MEMORY_MAX_CHARS
        assert "合并 1 条重复要求" in memory
        assert "原始记录全部保留" in memory and load_memory(second) == ""
        original = read_memory_history(first, "要求 1：")
        assert len(original) == 1 and original[0]["instruction"].endswith("原始细节" * 200)
        assert len(read_memory_history(first, limit=10)) == 10
        with connect() as conn:
            record_memory(conn, first, "未提交要求", "selection", "范围", 42)
            conn.rollback()
        assert "未提交要求" not in load_memory(first)
        with connect() as conn:
            record_memory(conn, second, "另一个文档要求", "document", "整篇文档", 1)
            clear_memory(conn, second)
            conn.commit()
        assert load_memory(second) == "" and read_memory_history(second) == []
        with connect() as conn:
            conn.execute("DELETE FROM documents WHERE id = %s", (first,))
            conn.commit()
        assert load_memory(first) == "" and read_memory_history(first) == []
    finally:
        clear_settings_cache()


def test_long_context_keeps_target_and_exposes_complete_whole_document_index():
    document = "# 前文\n\n" + "前文细节。" * 6000 + "\n\n# 目标\n\n必须完整保留的目标。\n\n# 后文\n\n" + "后文细节。" * 6000
    context = document_context(document, heading_index=1)
    assert "# 目标\n\n必须完整保留的目标。" in context
    assert "前文" in context and "后文" in context and "上下文已压缩" in context
    assert len(context) <= 24000
    whole = document_context(document, whole_document=True)
    assert all(f"[{index}] # {title}" in whole for index, title in enumerate(("前文", "目标", "后文")))
    assert "编辑前必须通过 read_document_section(index)" in whole and len(whole) <= 24000
    with pytest.raises(ValueError, match="目标章节超过"):
        document_context(document, heading_index=0)
    assert document_context("# 短文\n\n全文") == "# 短文\n\n全文"


@pytest.fixture
def memory_document(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'task-memory.db'}")
    clear_settings_cache()
    try:
        migrate()
        owner = create_user(account="task-memory", name="记忆", password="memory-password", roles=["system_admin"])
        project, document = uuid4(), uuid4()
        with connect() as connection:
            connection.execute("INSERT INTO projects (id, name, owner_id) VALUES (%s, %s, %s)",
                               (project, "任务记忆", owner))
            connection.execute("INSERT INTO documents (id, project_id, name, owner_id) VALUES (%s, %s, %s, %s)",
                               (document, project, "编写记忆", owner))
            connection.commit()
        yield document
    finally:
        clear_settings_cache()


def test_task_memory_recovers_early_target_while_new_constraints_take_priority(memory_document):
    with connect() as connection:
        record_memory(connection, memory_document, "身份校验须记录审计日志；旧版本要求使用中文。", "section", "身份校验", 1)
        record_memory(connection, memory_document, "不得编造性能数据。", "document", "整篇文档", 2)
        for revision in range(3, 123):
            record_memory(connection, memory_document, f"报表一次性要求 {revision}。", "section", "报表统计", revision)
        record_memory(connection, memory_document, "本次语言改为英文。", "section", "身份校验", 123)
        record_memory(connection, memory_document, "仅删掉选中句子的标点。", "selection", "身份校验", 124)
        connection.commit()
    # The UI recent summary may omit the old chapter, but this chapter's agent must retrieve it.
    assert "身份校验须记录" not in load_memory(memory_document)
    memory = load_task_memory(memory_document, scope="section", target="身份校验", instruction="继续完善本节。")
    assert "身份校验须记录审计日志" in memory and "不得编造性能数据" in memory
    assert memory.index("本次语言改为英文") < memory.index("旧版本要求使用中文")
    assert "本次要求优先" in memory and "历史冲突以较新版本优先" in memory
    assert "报表一次性要求" not in memory and "仅删掉选中句子" not in memory
    assert len(memory) <= MEMORY_MAX_CHARS


def test_early_and_global_requirements_survive_many_recent_requests_in_same_chapter(memory_document):
    with connect() as connection:
        record_memory(connection, memory_document, "早期约束：保留每次失败的审计日志。", "section", "登录", 1)
        record_memory(connection, memory_document, "整篇约束：未知事实标为待补充。", "document", "整篇文档", 2)
        for revision in range(3, 83):
            record_memory(connection, memory_document, f"近期约束 {revision}：" + "说明文本" * 50, "section", "登录", revision)
        connection.commit()
    memory = load_task_memory(memory_document, scope="section", target="登录")
    assert "早期约束：保留每次失败的审计日志。" in memory
    assert "整篇约束：未知事实标为待补充。" in memory and "近期约束 82" in memory
    assert len(memory) <= MEMORY_MAX_CHARS


def test_scope_boundaries_keep_selection_actions_local_and_label_whole_document_sections(memory_document):
    with connect() as connection:
        record_memory(connection, memory_document, "全篇术语使用统一名称。", "document", "整篇文档", 1)
        record_memory(connection, memory_document, "登录节保留审计记录。", "section", "登录", 2)
        record_memory(connection, memory_document, "报表节必须保留原始金额。", "section", "报表", 3)
        record_memory(connection, memory_document, "把这个句子临时缩成十字。", "selection", "登录", 4)
        connection.commit()
    selection = load_task_memory(memory_document, scope="selection", target="登录")
    assert "全篇术语" in selection and "登录节保留审计记录" in selection
    assert "报表节必须" not in selection and "临时缩成十字" not in selection
    assert "旧选区改写属于一次性操作" in selection and "不得超出所选范围" in selection
    whole = load_task_memory(memory_document, scope="document", target="整篇文档")
    assert "章节 登录：登录节保留审计记录。" in whole
    assert "章节 报表：报表节必须保留原始金额。" in whole
    assert "仅对标明的标题生效" in whole and "临时缩成十字" not in whole
    # One-off requests are excluded from automatic context, but remain exactly retrievable.
    assert read_memory_history(memory_document, "临时缩成")[0]["instruction"] == "把这个句子临时缩成十字。"


def test_over_budget_requests_remain_exactly_recoverable_without_fabricated_summary(memory_document):
    instruction = "完整要求开始\n" + "不得删改原始约束。" * 150 + "\n完整要求结束"
    with connect() as connection:
        record_memory(connection, memory_document, instruction, "section", "接口", 1)
        connection.commit()
    memory = load_task_memory(memory_document, scope="section", target="接口", max_chars=1000)
    assert len(memory) <= 1000 and "超预算" in memory and "search_writing_memory" in memory
    assert "完整要求开始" not in memory  # An incomplete quote must not masquerade as a full requirement.
    assert read_memory_history(memory_document, "完整要求开始")[0]["instruction"] == instruction
    assert instruction in load_task_memory(memory_document, scope="section", target="接口")
    with pytest.raises(ValueError, match="上下文预算"):
        load_task_memory(memory_document, scope="section", target="接口", max_chars=999)


def test_long_history_keeps_summary_and_candidate_reads_bounded_and_merges_duplicates(memory_document, monkeypatch):
    with connect() as connection:
        for revision in range(1, 3001):
            connection.execute(
                "INSERT INTO document_memory_events (document_id, revision, scope, target, instruction) VALUES (%s, %s, %s, %s, %s)",
                (memory_document, revision, "section", "正文", "统一术语。"),
            )
        connection.commit()
    import app.document_memory as module
    queries = []

    @contextmanager
    def monitored_connect():
        with connect() as connection:
            class MonitoredConnection:
                def __getattr__(self, name):
                    return getattr(connection, name)

                def execute(self, sql, params=()):
                    if "SELECT" in sql and "FROM document_memory_events" in sql:
                        assert "LIMIT %s" in sql
                        queries.append(params[-1])
                    return connection.execute(sql, params)
            yield MonitoredConnection()

    monkeypatch.setattr(module, "connect", monitored_connect)
    with connect() as connection:
        # Caller owns this transaction; instrument the one summary SELECT as well.
        original_execute = connection.execute
        def monitored_execute(sql, params=()):
            if "SELECT" in sql and "FROM document_memory_events" in sql:
                assert "LIMIT %s" in sql
                queries.append(params[-1])
            return original_execute(sql, params)
        connection.execute = monitored_execute
        record_memory(connection, memory_document, "统一术语。", "section", "正文", 3001)
        connection.commit()
    summary = load_memory(memory_document)
    assert "合并 79 条重复要求" in summary and "更早记录未纳入近期摘要" in summary
    task = load_task_memory(memory_document, scope="section", target="正文")
    assert task.count("统一术语。") == 1 and "版本 3001" in task
    assert queries == [SUMMARY_ROW_LIMIT + 1, TASK_RECENT_ROW_LIMIT, TASK_RECENT_ROW_LIMIT,
                       TASK_EARLY_ROW_LIMIT, TASK_EARLY_ROW_LIMIT]


def _save_memory_document(document, text, **kwargs):
    from app.documents import _save_markdown
    with connect() as connection:
        revision = connection.execute("SELECT revision FROM documents WHERE id=%s", (document,)).fetchone()[0]
        _save_markdown(connection, document, text, revision, **kwargs)
        connection.commit()


def _targets(document):
    with connect() as connection:
        return section_memory_targets(connection, document)


def _legacy_memory(document, title, instruction, revision):
    with connect() as connection:
        connection.execute("""INSERT INTO document_memory_events(document_id,revision,scope,target,instruction)
                              VALUES (%s,%s,'section',%s,%s)""", (document, revision, title, instruction))
        connection.commit()


def test_same_named_sections_use_distinct_stable_memory_and_hide_internal_ids(memory_document):
    _save_memory_document(memory_document, "# 模块甲\n\n## 接口\n\n甲正文。\n\n# 模块乙\n\n## 接口\n\n乙正文。")
    targets = _targets(memory_document)
    with connect() as connection:
        record_memory(connection, memory_document, "甲模块使用甲协议。", "section", targets[1], 1)
        record_memory(connection, memory_document, "乙模块使用乙协议。", "section", targets[3], 2)
        connection.commit()
    first = load_task_memory(memory_document, scope="section", target=targets[1])
    second = load_task_memory(memory_document, scope="selection", target=targets[3])
    assert "甲模块使用甲协议。" in first and "乙模块使用乙协议。" not in first
    assert "乙模块使用乙协议。" in second and "甲模块使用甲协议。" not in second
    ambiguous = load_task_memory(memory_document, scope="section", target="接口")
    assert "甲模块使用甲协议。" not in ambiguous and "乙模块使用乙协议。" not in ambiguous
    assert "同名" in ambiguous
    whole = load_task_memory(memory_document, scope="document", target="全文")
    assert "模块甲 › 接口" in whole and "模块乙 › 接口" in whole
    assert "heading:" not in whole and "heading:" not in load_memory(memory_document)
    history = read_memory_history(memory_document)
    assert {row["target"] for row in history} == {"模块甲 › 接口", "模块乙 › 接口"}


def test_section_memory_follows_heading_rename_and_explicit_move(memory_document):
    from app.document_objects import live_objects
    from app.markdown_blocks import Block, assemble
    text = "# 模块甲\n\n## 接口\n\n甲正文。\n\n# 模块乙\n\n乙正文。"
    _save_memory_document(memory_document, text)
    target = _targets(memory_document)[1]
    with connect() as connection:
        record_memory(connection, memory_document, "保留接口校验。", "section", target, 1)
        connection.commit()
    _save_memory_document(memory_document, text.replace("## 接口", "## 接口设计"))
    renamed = _targets(memory_document)[1]
    assert renamed.split("|")[0] == target.split("|")[0]
    assert "保留接口校验。" in load_task_memory(memory_document, scope="section", target=renamed)
    with connect() as connection:
        rows = live_objects(connection, memory_document)
        boundary = next(index for index, row in enumerate(rows) if row["body"] == "# 模块乙")
        moved = rows[boundary:] + rows[:boundary]
        text = assemble("", [Block(row["kind"], row["body"], row["gap_after"] or "\n\n", row["heading_level"]) for row in moved])
    ids = [str(row["id"]) for row in moved]
    _save_memory_document(memory_document, text, identities=ids, moved=set(ids))
    after_move = _targets(memory_document)[2]
    assert after_move.split("|")[0] == target.split("|")[0]
    assert "保留接口校验。" in load_task_memory(memory_document, scope="section", target=after_move)


def test_unique_legacy_memory_survives_rename_without_rewriting_original_record(memory_document):
    text = "# 模块甲\n\n## 接口\n\n正文。"
    _save_memory_document(memory_document, text)
    _legacy_memory(memory_document, "接口", "旧记录的有效要求。", 1)
    _save_memory_document(memory_document, text.replace("## 接口", "## 新接口"))
    current = _targets(memory_document)[1]
    assert "旧记录的有效要求。" in load_task_memory(memory_document, scope="section", target=current)
    with connect() as connection:
        assert connection.execute("SELECT target FROM document_memory_events WHERE document_id=%s", (memory_document,)).fetchone()[0] == "接口"


def test_legacy_memory_without_source_version_stays_available_but_is_not_guessed(memory_document):
    _save_memory_document(memory_document, "# 范围\n\n正文。")
    _legacy_memory(memory_document, "范围", "缺少来源版本的旧要求。", 0)
    with connect() as connection:
        connection.execute("DELETE FROM document_versions WHERE document_id=%s AND revision=0", (memory_document,))
        connection.commit()
    target = _targets(memory_document)[0]
    assert "缺少来源版本的旧要求。" not in load_task_memory(memory_document, scope="section", target=target)
    assert "未定位" in read_memory_history(memory_document)[0]["target"]


def test_memory_rejects_heading_ids_outside_its_document(memory_document):
    from app.errors import AppError
    _save_memory_document(memory_document, "# 范围\n\n正文。")
    with connect() as connection:
        with pytest.raises(AppError) as error:
            record_memory(connection, memory_document, "不能写入错误对象。", "section", f"heading:{uuid4()}|范围", 1)
        assert error.value.code == "MEMORY_TARGET_INVALID"
        assert connection.execute("SELECT COUNT(*) FROM document_memory_events WHERE document_id=%s", (memory_document,)).fetchone()[0] == 0


def test_stable_target_keeps_original_human_title_budget(memory_document):
    title = "接口说明" * 30
    _save_memory_document(memory_document, f"# {title}\n\n正文。")
    target = _targets(memory_document)[0]
    with connect() as connection:
        record_memory(connection, memory_document, "长标题的有效要求。", "section", target, 1)
        connection.commit()
    assert read_memory_history(memory_document)[0]["target"] == title
    assert "长标题的有效要求。" in load_task_memory(memory_document, scope="section", target=target)


def test_ambiguous_legacy_memory_never_attaches_after_one_duplicate_is_deleted(memory_document):
    text = "# 模块甲\n\n## 接口\n\n甲正文。\n\n# 模块乙\n\n## 接口\n\n乙正文。"
    _save_memory_document(memory_document, text)
    _legacy_memory(memory_document, "接口", "无法确定属于甲还是乙的旧要求。", 1)
    for target in (_targets(memory_document)[1], _targets(memory_document)[3]):
        assert "无法确定属于甲还是乙的旧要求。" not in load_task_memory(memory_document, scope="section", target=target)
    assert "未定位" in read_memory_history(memory_document)[0]["target"]
    _save_memory_document(memory_document, "# 模块乙\n\n## 接口\n\n乙正文。")
    assert "无法确定属于甲还是乙的旧要求。" not in load_task_memory(memory_document, scope="section", target=_targets(memory_document)[1])
    whole = load_task_memory(memory_document, scope="document", target="全文")
    assert "无法确定属于甲还是乙的旧要求。" not in whole and "同名" in whole


@pytest.mark.parametrize("allow_merge", [False, True])
def test_section_adoption_and_draft_bind_memory_to_heading_objects(memory_document, monkeypatch, allow_merge):
    from types import SimpleNamespace
    import app.documents as documents
    text = "# 模块甲\n\n## 接口\n\n甲正文。\n\n# 模块乙\n\n## 接口\n\n乙正文。"
    _save_memory_document(memory_document, text)
    with connect() as connection:
        owner = connection.execute("SELECT owner_id FROM documents WHERE id=%s", (memory_document,)).fetchone()[0]
    monkeypatch.setattr(documents, "document_principal", lambda *args: SimpleNamespace(user_id=owner, roles=("system_admin",)))
    monkeypatch.setattr(documents, "_check_word", lambda *args, **kwargs: None)
    monkeypatch.setattr(documents, "require_ai", lambda *args: None)
    request = SimpleNamespace(state=SimpleNamespace(request_id="stable-memory-test"))
    for revision, index, instruction in ((1, 1, "甲模块的采纳要求。"), (2, 3, "乙模块的采纳要求。")):
        result = documents.insert_section(request, str(memory_document), documents.SectionInsertBody(
            expectedRevision=revision, headingIndex=index, content="已经确认的接口正文。", mode="replace",
            instruction=instruction, allowMerge=allow_merge, clientSaveKey=f"section-memory-{revision}" if allow_merge else None))
        assert result["data"]["revision"] == revision + 1
    targets = _targets(memory_document)
    assert "甲模块的采纳要求。" not in load_task_memory(memory_document, scope="section", target=targets[3])
    assert "乙模块的采纳要求。" in load_task_memory(memory_document, scope="section", target=targets[3])
    captured = {}
    def draft(**kwargs):
        captured.update(kwargs)
        return section_answer(kwargs, "草稿中的接口正文。")
    monkeypatch.setattr(documents, "draft_section_text", draft)
    documents.draft_section(request, str(memory_document), documents.SectionDraftBody(expectedRevision=3, headingIndex=3))
    assert captured["memory_targets"][3] == targets[3]
