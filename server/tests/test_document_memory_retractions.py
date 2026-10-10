"""Undo restores text and retracts only the adopted request in one transaction."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.document_memory as memory_module
import app.documents as documents
from app.config import clear_settings_cache
from app.db import connect, migrate
from app.document_memory import clear_memory, load_memory, load_task_memory, read_memory_history
from app.errors import AppError
from app.identity import create_user


@pytest.fixture
def adopted_document(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'retractions.db'}")
    clear_settings_cache()
    try:
        migrate()
        owner = create_user(account="retraction-owner", name="撤回测试", password="retraction-password", roles=["project_creator"])
        project, document = uuid4(), uuid4()
        with connect() as connection:
            connection.execute("INSERT INTO projects (id, name, owner_id) VALUES (%s, %s, %s)", (project, "中性项目", owner))
            connection.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (%s, %s, 'owner')", (project, owner))
            connection.execute("INSERT INTO documents (id, project_id, name, owner_id) VALUES (%s, %s, %s, %s)",
                               (document, project, "中性文档", owner))
            connection.commit()
        monkeypatch.setattr(documents, "document_principal", lambda *args: SimpleNamespace(user_id=owner, roles=("project_creator",)))
        monkeypatch.setattr(documents, "_check_word", lambda *args: None)
        request = SimpleNamespace(state=SimpleNamespace(request_id="retraction-test"))
        for revision, content, instruction in ((0, "# 范围\n\n原正文。", "保持旧术语。"),
                                               (1, "# 范围\n\nAI 修改正文。", "本次改用新术语。")):
            documents.update_document_content(request, str(document), documents.DocumentContentPatchBody(
                expectedRevision=revision, markdownContent=content,
                memory=documents.WritingMemoryBody(instruction=instruction, scope="section", target="范围")))
        yield request, document
    finally:
        clear_settings_cache()


def state(document):
    with connect() as connection:
        row = connection.execute("SELECT revision, markdown_content FROM documents WHERE id = %s", (document,)).fetchone()
        retracted = connection.execute("SELECT revision FROM document_memory_retractions WHERE document_id = %s ORDER BY revision", (document,)).fetchall()
    return row["revision"], row["markdown_content"], [item["revision"] for item in retracted]


def restore(request, document, revision=2, retract=2, **kwargs):
    return documents.update_document_content(request, str(document), documents.DocumentContentPatchBody(
        expectedRevision=revision, markdownContent="# 范围\n\n原正文。", retractMemoryRevision=retract, **kwargs))


def test_undo_hides_only_current_request_from_summary_history_and_agent_memory(adopted_document):
    request, document = adopted_document
    restored = restore(request, document)
    assert restored["data"]["revision"] == 3
    assert state(document) == (3, "# 范围\n\n原正文。", [2])
    assert "保持旧术语。" in load_memory(document) and "本次改用新术语。" not in load_memory(document)
    task = load_task_memory(document, scope="section", target="范围")
    assert "保持旧术语。" in task and "本次改用新术语。" not in task
    assert [item["revision"] for item in read_memory_history(document)] == [1]
    assert read_memory_history(document, query="新术语") == []
    with connect() as connection:
        assert connection.execute("SELECT count(*) AS total FROM document_memory_events WHERE document_id = %s", (document,)).fetchone()["total"] == 2
    inspection = documents.get_writing_memory(request, str(document), offset=0, limit=10)["data"]
    assert inspection["items"] == [{"target": "范围", "instruction": "保持旧术语。"}] and not inspection["hasMore"]
    with pytest.raises(AppError) as stale:
        restore(request, document)
    assert stale.value.status_code == 409 and state(document)[2] == [2]
    with connect() as connection:
        clear_memory(connection, document)
        connection.commit()
    assert state(document)[2] == [1, 2] and load_memory(document) == "" and read_memory_history(document) == []


@pytest.mark.parametrize("revision,retract,additional", [
    (2, 1, {}),
    (2, 2, {"memory": documents.WritingMemoryBody(instruction="不能同时写入。", scope="section", target="范围")}),
])
def test_undo_cannot_retract_an_older_requirement_or_add_a_new_one(adopted_document, revision, retract, additional):
    request, document = adopted_document
    before = state(document), load_memory(document)
    with pytest.raises(AppError) as rejected:
        restore(request, document, revision=revision, retract=retract, **additional)
    assert rejected.value.status_code == 422
    assert (state(document), load_memory(document)) == before


def test_failed_word_validation_does_not_retract_memory(adopted_document, monkeypatch):
    request, document = adopted_document
    before = state(document), load_memory(document)

    def fail(*args):
        raise AppError(422, "AI_FORMAT_INVALID", "不能安全恢复该段。")

    monkeypatch.setattr(documents, "_check_word", fail)
    with pytest.raises(AppError):
        restore(request, document)
    assert (state(document), load_memory(document)) == before


def test_concurrent_save_wins_without_retracting_stale_memory(adopted_document, monkeypatch):
    request, document = adopted_document
    before = load_memory(document)

    def concurrent(*args):
        with connect() as writer:
            writer.execute("UPDATE documents SET revision=revision+1, markdown_content=%s WHERE id=%s",
                           ("# 范围\n\n其他窗口保存的正文。", document))
            writer.commit()

    monkeypatch.setattr(documents, "_check_word", concurrent)
    with pytest.raises(AppError) as stale:
        restore(request, document)
    assert stale.value.status_code == 409
    assert state(document) == (3, "# 范围\n\n其他窗口保存的正文。", []) and load_memory(document) == before


def test_summary_failure_rolls_back_both_document_restore_and_retraction(adopted_document, monkeypatch):
    request, document = adopted_document
    before = state(document), load_memory(document)

    def fail(*args):
        raise RuntimeError("isolated summary failure")

    monkeypatch.setattr(memory_module, "_refresh_summary", fail)
    with pytest.raises(RuntimeError):
        restore(request, document)
    assert (state(document), load_memory(document)) == before


def test_retraction_without_an_explicit_instruction_is_a_safe_noop(adopted_document):
    request, document = adopted_document
    documents.update_document_content(request, str(document), documents.DocumentContentPatchBody(
        expectedRevision=2, markdownContent="# 范围\n\n未指定额外要求的修改。"))
    before = load_memory(document)
    restore(request, document, revision=3, retract=3)
    assert state(document) == (4, "# 范围\n\n原正文。", []) and load_memory(document) == before
