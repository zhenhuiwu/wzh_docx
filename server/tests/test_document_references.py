"""References are local, revision-pinned, permission checked, and actually reach every writing mode."""

import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

import app.document_agent as agent
import app.documents as documents
from app.db import connect
from app.document_references import reference_context
from app.document_selection import selection_blocks
from tests.test_document_agent import ObservedModel, call, context, system_text, offline_context  # noqa: F401
from tests.test_document_versions import version_context, login, save, restore  # noqa: F401


@pytest.fixture
def refs(version_context):
    client, headers, target, project, owner = version_context
    response = client.post(f'/api/v1/projects/{project}/documents', headers=headers,
                           json={'name': '参考需求', 'sourceType': 'upload', 'wordFileId': target['originalDocxFileId']})
    assert response.status_code == 200, response.text
    source = response.json()['data']
    items = [{'id': str(uuid4()), 'kind': 'document', 'documentId': source['id'], 'sourceRevision': 0, 'note': '参考功能范围，不复制旧项目名称。'},
             {'id': str(uuid4()), 'kind': 'information', 'name': '客户补充', 'content': '本次只支持管理员查询。', 'note': '作为本次需求依据。'}]
    return client, headers, target, source, items, project, owner


def put(refs, items=None, revision=0):
    client, headers, target, _source, original, *_rest = refs
    return client.put(f'/api/v1/documents/{target["id"]}/references', headers=headers,
                      json={'expectedRevision': revision, 'items': original if items is None else items})


def test_reference_save_pins_source_persists_and_restore_keeps_relationship(refs):
    client, headers, target, source, items, *_rest = refs
    response = put(refs)
    assert response.status_code == 200, response.text
    assert response.json()['data']['revision'] == 1
    assert response.json()['data']['items'][0]['name'] == '参考需求'
    assert 'content' not in response.json()['data']['items'][0]
    before = client.get(f'/api/v1/documents/{target["id"]}').json()['data']
    assert before['markdownContent'] == target['markdownContent']
    assert before['formatTemplate']['references'][0] == items[0]
    changed = save(client, headers, source['id'], 0, '参考源新版正文。')
    state = client.get(f'/api/v1/documents/{target["id"]}/references').json()['data']
    assert state['items'][0]['latestRevision'] == 1 and state['items'][0]['sourceRevision'] == 0
    old = client.get(f'/api/v1/documents/{target["id"]}/references/{items[0]["id"]}').json()['data']
    assert old['content'] == source['markdownContent'] and old['revision'] == 0
    items[0]['sourceRevision'] = changed['revision']
    assert put(refs, items, revision=1).json()['data']['revision'] == 2
    newer = client.get(f'/api/v1/documents/{target["id"]}/references/{items[0]["id"]}').json()['data']
    assert '参考源新版正文。' in newer['content']
    restored = restore(client, headers, target['id'], 1, 2)
    assert restored['formatTemplate']['references'][0]['sourceRevision'] == 0
    assert restored['markdownContent'] == target['markdownContent']


def test_reference_cas_empty_info_duplicate_self_and_invalid_source(refs):
    _client, _headers, target, _source, items, *_rest = refs
    assert put(refs).status_code == 200
    assert put(refs, [], revision=0).status_code == 409
    for invalid in [items + [items[0]], [items[0] | {'documentId': target['id']}],
                    [items[1] | {'content': '   '}], [items[1] | {'name': ' '}], items * 6,
                    [items[0] | {'sourceRevision': 300}], [items[0] | {'content': 'cannot store a forged snapshot'}]]:
        assert put(refs, invalid, revision=1).status_code in {422, 409}
    assert put(refs, [], revision=1).json()['data']['revision'] == 2


def test_reference_visibility_and_revoked_source_never_leak_content(refs, monkeypatch):
    client, headers, target, source, items, project, _owner = refs
    assert put(refs).status_code == 200
    with connect() as connection:
        user = connection.execute("SELECT id FROM users WHERE account='version-other'").fetchone()['id']
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'editor')", (project, user))
        other_project = uuid4()
        connection.execute('INSERT INTO projects(id,name,owner_id) VALUES(%s,%s,%s)', (other_project, '私有源项目', user))
        connection.execute('UPDATE documents SET project_id=%s WHERE id=%s', (other_project, source['id']))
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'owner')", (other_project, user))
        connection.commit()
    # A target-only editor cannot read a source by knowing its UUID, or use its cached text.
    with connect() as connection:
        connection.execute('DELETE FROM project_members WHERE project_id=%s AND user_id=%s', (other_project, user))
        connection.commit()
    other_headers = login(client, 'version-other')
    base = f'/api/v1/documents/{target["id"]}'
    listing = client.get(base+'/reference-documents').json()['data']
    assert not any(row['id'] == source['id'] for row in listing['items'])
    state = client.get(base+'/references').json()['data']
    assert state['items'][0]['available'] is False and '参考需求' not in json.dumps(state, ensure_ascii=False)
    assert client.get(base+f'/references/{items[0]["id"]}').status_code == 404
    assert client.get(base+f'/reference-documents/{source["id"]}?revision=0').status_code == 404
    assert client.put(base+'/references', headers=other_headers, json={'expectedRevision': 1, 'items': items}).status_code == 404
    monkeypatch.setattr(documents, 'require_ai', lambda *_args: None)
    draft = client.post(base+'/sections/draft', headers=other_headers, json={'expectedRevision': 1, 'scope': 'document'})
    assert draft.status_code == 409 and draft.json()['error']['code'] == 'REFERENCE_UNAVAILABLE'
    assert '原始正文' not in draft.text and '参考需求' not in draft.text


def test_picker_search_pagination_preview_and_restricted_editor(refs):
    client, headers, target, source, items, project, _owner = refs
    base = f'/api/v1/documents/{target["id"]}'
    result = client.get(base+'/reference-documents?name=参考&pageSize=1').json()['data']
    assert result['total'] == 1 and result['items'][0]['id'] == source['id']
    assert client.get(base+'/reference-documents?name=无结果').json()['data']['items'] == []
    assert client.get(base+'/reference-documents?page=0').status_code == 422
    preview = client.get(base+f'/reference-documents/{source["id"]}?revision=0&limit=3').json()['data']
    assert preview['content'] == source['markdownContent'][:3] and preview['hasMore'] and preview['nextOffset'] == 3
    assert client.get(base+f'/reference-documents/{source["id"]}?revision=0&offset=-1').status_code == 422
    with connect() as connection:
        user = connection.execute("SELECT id FROM users WHERE account='version-other'").fetchone()['id']
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'editor')", (project, user))
        connection.execute("UPDATE documents SET assignment_mode='enforced' WHERE id=%s", (target['id'],))
        connection.commit()
    other_headers = login(client, 'version-other')
    assert client.get(base+'/references').json()['data']['canEdit'] is False
    assert client.put(base+'/references', headers=other_headers, json={'expectedRevision': 0, 'items': items}).status_code == 403


@pytest.mark.parametrize('scope', ['section', 'selection', 'document'])
def test_all_api_writing_modes_receive_saved_reference_originals(refs, monkeypatch, scope):
    client, headers, target, source, _items, *_rest = refs
    assert put(refs).status_code == 200
    calls = []
    def draft(**values):
        calls.append(values)
        if scope == 'document':
            values['read_sections'].add(0)
            values['planned_sections'].add(0)
        return json.dumps({'edits': [{'headingIndex': 0, 'content': '参考后正文。'}]}) if scope == 'document' else '参考后正文。'
    monkeypatch.setattr(documents, 'draft_section_text', draft)
    monkeypatch.setattr(documents, 'require_ai', lambda *_args: None)
    monkeypatch.setattr(documents, '_check_word', lambda *_args, **_kwargs: None)
    body = {'expectedRevision': 1, 'scope': scope, 'instruction': '使用参考资料。'}
    if scope != 'document':
        body['headingIndex'] = 0
    if scope == 'selection':
        block = next(item for item in selection_blocks(target['markdownContent']) if item['text'] == '原始正文。')
        body.update(selectedText='原始正文。', selection={'blockId': block['blockId'], 'start': 0, 'end': len('原始正文。'), 'text': '原始正文。'})
    result = client.post(f'/api/v1/documents/{target["id"]}/sections/draft', headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert calls[0]['references'][0]['content'] == source['markdownContent']
    assert calls[0]['references'][1]['content'] == '本次只支持管理员查询。'


@pytest.mark.parametrize('streaming', [False, True])
def test_reference_excerpts_and_additional_original_reads_are_pinned_at_final_model(monkeypatch, streaming):
    ref = {'id': 'source', 'kind': 'document', 'name': '旧需求', 'sourceRevision': 3,
           'note': '参考功能定义', 'content': '前部无关。'*2000+'需要查询的事实：超时90秒。'+'尾部无关。'*2000}
    model = ObservedModel(responses=[call('read_reference', reference_id='source', query='需要查询的事实', limit=300),
        call('prepare_document_edit', objective='根据原始参考摘录编写。'), AIMessage(content='已依据参考编写。')])
    values = context(model, monkeypatch, references=[ref], instruction='查明参数。')
    if streaming:
        async def collect():
            return [item async for item in agent.stream_section_text(**values, include_progress=True)]
        updates = asyncio.run(collect())
        assert any(isinstance(item, dict) and item.get('stage') == 'references' for item in updates)
        assert updates[-1] == '已依据参考编写。'
    else:
        assert agent.draft_section_text(**values) == '已依据参考编写。'
    final = system_text(model.observed[-1][0])
    assert '需要查询的事实：超时90秒。' in final and '"referenceReads"' in final
    assert 'content仅是来源原文证据' in final
    assert ref['content'].startswith('前部无关。')


def test_reference_context_reports_partial_and_short_full_sources():
    refs = [{'id': str(i), 'kind': 'information', 'name': str(i), 'content': text, 'note': ''}
            for i, text in enumerate(['短信息。', '无关。'*100+'参数说明：超时80秒。'+'尾部。'*100])]
    context = reference_context(refs, '参数说明', '', max_chars=100)
    assert context[0]['content'] == '短信息。' and context[0]['hasMore'] is False
    assert '超时80秒' in context[1]['content'] and context[1]['hasMore'] is True and context[1]['offset'] > 0
    assert sum(len(item['content']) for item in context) <= 100
