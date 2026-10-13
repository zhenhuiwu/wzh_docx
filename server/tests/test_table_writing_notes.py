"""Table legends remain in Word and are editable advice for their own table."""
import pytest
from langchain_core.messages import AIMessage

from app.db import connect
from app.document_guidance import effective_advice, writing_context
from app.document_rules import table_writing_notes, validate_draft, DraftTableError
from tests.test_document_versions import version_context  # noqa: F401
from tests.test_writing_guidance import query, patch
from tests.test_document_agent import context, ObservedModel, call, system_text, offline_context  # noqa: F401
from app.document_agent import draft_section_text

NOTE = '注：合格性方法：<br>A-演示；<br>B-测试；<br>合格性级别：<br>a-配置项；<br>b-系统集成。'
TABLE = '| 需求名称 | 方法 |\n| --- | --- |\n| 登录 |  |\n| ' + NOTE + ' | ' + NOTE + ' |'


def test_merged_note_is_deduplicated_and_must_remain_in_word():
    notes = table_writing_notes(TABLE)
    assert notes.count('A-演示') == 1 and 'b-系统集成' in notes
    assert validate_draft(TABLE.replace('| 登录 |  |', '| 登录 | B |'), {}, existing=TABLE)
    with pytest.raises(DraftTableError, match='说明'):
        validate_draft(TABLE.replace('A-演示', 'A-测试'), {}, existing=TABLE)


def test_existing_document_notes_are_visible_editable_resettable_and_scoped(version_context):
    client, headers, doc, _, _ = version_context
    markdown = '# 范围\n\n' + TABLE + '\n\n# 其他\n\n其他正文。'
    with connect() as connection:
        connection.execute('UPDATE documents SET markdown_content=%s WHERE id=%s', [markdown, doc['id']])
        connection.commit()
    result = query(client, headers, doc['id'])
    assert result.status_code == 200, result.text
    items = result.json()['data']['groups'][0]['items']
    assert len(items) == 1 and items[0]['heading'] == '表格填写说明'
    item = items[0]
    assert 'A-演示' in item['sourceText'] and item['text'] == item['sourceText'] and not item['overridden']
    assert query(client, headers, doc['id'], headingIndex=1).json()['data']['groups'][0]['items'] == []
    response = patch(client, headers, doc['id'], 0, [{'id': item['id'], 'text': '本次仅使用 B-测试，级别 a。'}])
    assert response.status_code == 200, response.text
    updated = query(client, headers, doc['id'], revision=1).json()['data']['groups'][0]['items'][0]
    assert updated['overridden'] and updated['text'] == '本次仅使用 B-测试，级别 a。'
    assert updated['sourceText'] == item['sourceText']
    assert client.get(f'/api/v1/documents/{doc["id"]}').json()['data']['markdownContent'] == markdown
    reset = patch(client, headers, doc['id'], 1, [{'id': item['id'], 'text': None}])
    assert reset.status_code == 200
    assert query(client, headers, doc['id'], revision=2).json()['data']['groups'][0]['items'][0]['text'] == item['sourceText']


def test_notes_only_reach_the_owning_table_and_final_model(monkeypatch):
    markdown = '# 范围\n\n' + TABLE + '\n\n普通说明。'
    objects = [{'id': 'table', 'kind': 'table', 'body': TABLE, 'start': markdown.index(TABLE),
                'end': markdown.index(TABLE)+len(TABLE), 'headingIndex': 0, 'label': '表格'},
               {'id': 'paragraph', 'kind': 'paragraph', 'body': '普通说明。', 'start': markdown.index('普通说明。'),
                'end': len(markdown), 'headingIndex': 0, 'label': '普通说明。'}]
    template = {'blockGuidance': {'table': '只填写 B 和 a。'}}
    assert effective_advice(template, markdown, 0, objects, ['paragraph']) == []
    scoped = effective_advice(template, markdown, 0, objects, ['table'])
    assert scoped[0]['text'] == '只填写 B 和 a。' and 'A-演示' in scoped[0]['sourceText']
    modules = writing_context(template, markdown, objects)[0]['modules']
    assert modules[0]['guidance'] == scoped and modules[1]['guidance'] == []
    model = ObservedModel(responses=[call('prepare_document_edit', objective='遵守表格说明。'), AIMessage(content=TABLE)])
    assert draft_section_text(**context(model, monkeypatch, existing=TABLE, full_markdown=markdown,
                                        effective_guidance=scoped)) == TABLE
    prompt = system_text(model.observed[-1][0])
    assert '只填写 B 和 a。' in prompt and 'A-演示' in prompt
