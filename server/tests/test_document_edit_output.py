"""Whole editing changes exact source spans; malformed headings never reach Word."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

import app.document_agent as agent
from tests.test_document_agent import ObservedModel, call, context, system_text, offline_context  # noqa: F401


def run(values, streaming):
    if not streaming:
        return agent.draft_section_text(**values)
    async def collect():
        return [item async for item in agent.stream_section_text(**values, include_progress=True)]
    result = asyncio.run(collect())
    return [item for item in result if isinstance(item, str)][-1]


@pytest.mark.parametrize('streaming', [False, True])
def test_whole_edit_changes_one_fact_and_preserves_all_other_bytes(monkeypatch, streaming):
    source = '名称：已有系统。\n\n**超时：30 秒。**\n\n| 功能 | 结果 |\n| --- | --- |\n| 登录 | 成功 |\n\n保留这段结论。'
    model = ObservedModel(responses=[call('prepare_document_edit', objective='仅调整超时。'),
        AIMessage(content=json.dumps({'replacements': [{'before': '30 秒', 'after': '60 秒'}]}))])
    values = context(model, monkeypatch, whole_document=True, sequential_document=True, edit_in_place=True,
                     heading='全文', heading_index=None, existing='', full_markdown='# 范围\n\n'+source,
                     instruction='只将超时30秒改为60秒，其他内容不动。')
    result = json.loads(run(values, streaming))
    assert result == {'edits': [{'headingIndex': 0, 'content': source.replace('30 秒', '60 秒')}]}
    assert '不能将它当作空白模板从头编写' in system_text(model.observed[-1][0])


@pytest.mark.parametrize('streaming', [False, True])
def test_noop_replacements_do_not_create_whole_document_rewrite(monkeypatch, streaming):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='内容符合要求，不修改。'),
                                     AIMessage(content='{"replacements":[]}')])
    values = context(model, monkeypatch, whole_document=True, sequential_document=True, edit_in_place=True)
    assert json.loads(run(values, streaming)) == {'edits': []}


@pytest.mark.parametrize('streaming', [False, True])
def test_plain_rewrite_is_corrected_to_exact_replacement_once(monkeypatch, streaming):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='仅修改一处。'),
        AIMessage(content='这是一整篇新写的文档，遗漏原有表格。'),
        AIMessage(content='{"replacements":[{"before":"原文", "after":"内容"}]}')])
    values = context(model, monkeypatch, edit_in_place=True)
    assert run(values, streaming) == '必须保留的完整内容。'
    assert len(model.observed) == 3


@pytest.mark.parametrize('bad', [
    {'replacements': [{'before': '缺失', 'after': '新'}]},
    {'replacements': [{'before': '重复', 'after': '新'}]},
    {'replacements': [{'before': '', 'after': '新'}]},
    {'replacements': [{'before': '重复。', 'after': '新'}, {'before': '复。', 'after': '新'}]},
    {'replacements': [{'before': '结尾', 'after': 5}]},
])
def test_unmatched_ambiguous_overlapping_and_invalid_patch_never_rewrites(bad):
    with pytest.raises(agent.DraftFormatError):
        agent._apply_replacements(json.dumps(bad), '重复。重复，结尾')


def test_overlapping_occurrences_are_not_a_unique_source_match():
    with pytest.raises(agent.DraftFormatError, match='唯一'):
        agent._apply_replacements('{"replacements":[{"before":"aaa","after":"b"}]}', 'aaaa')


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('body', ['# 目标\n\n正确正文。', '目标\n===\n\n正确正文。'])
def test_matching_heading_wrapper_is_removed_without_model_retry(monkeypatch, streaming, body):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='修改正文。'), AIMessage(content=body)])
    assert run(context(model, monkeypatch), streaming) == '正确正文。'
    assert len(model.observed) == 2


@pytest.mark.parametrize('streaming', [False, True])
def test_new_heading_repaired_once_without_dropping_its_content(monkeypatch, streaming):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='保留说明内容。'),
        AIMessage(content='### 新增小标题\n\n需要保留的说明。'),
        AIMessage(content='**新增小标题**\n\n需要保留的说明。')])
    assert run(context(model, monkeypatch), streaming) == '**新增小标题**\n\n需要保留的说明。'
    assert len(model.observed) == 3


def test_stream_hides_partial_headings_and_unfixed_errors(monkeypatch):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='编写。'),
        AIMessage(content='# 意外标题\n\n说明。'), AIMessage(content='# 仍是标题\n\n说明。')])
    async def collect():
        output = []
        with pytest.raises(agent.DraftFormatError, match='标题'):
            async for item in agent.stream_section_text(**context(model, monkeypatch)):
                output.append(item)
        assert not output
    asyncio.run(collect())
    assert len(model.observed) == 3


def test_prepared_block_source_does_not_expand_back_to_whole_chapter(monkeypatch):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='只改第一段。'),
        AIMessage(content='{"replacements":[{"before":"第一段", "after":"第一段修改后"}]}')])
    values = context(model, monkeypatch, edit_in_place=True, existing='第一段。',
                     full_markdown='# 目标\n\n第一段。\n\n第二段不能改。')
    instance, inputs, prepared = agent._section_agent(**values)
    instance.invoke(inputs, config={'recursion_limit':100})
    source = json.loads(prepared['source'])
    assert source['sections'][0]['content'] == '第一段。'


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('mode', ['chapter', 'selection', 'whole'])
def test_word_cell_breaks_survive_every_writing_mode(monkeypatch, streaming, mode):
    source = '| 功能 | 要求 |\n| --- | --- |\n| 查询 | 第一行<br>超时30秒 |'
    expected = source.replace('30秒', '60秒')
    values = dict(existing=source, full_markdown='# 目标\n\n' + source)
    if mode == 'whole':
        # Quote the break in before: this is source evidence, not generated HTML.
        output = json.dumps({'replacements': [{'before': '第一行<br>超时30秒', 'after': '第一行<br>超时60秒'}]})
        values.update(whole_document=True, sequential_document=True, edit_in_place=True)
    elif mode == 'selection':
        values.update(existing='超时30秒', selected_only=True)
        output = expected = '超时60秒'
    else:
        output = expected
    model = ObservedModel(responses=[call('prepare_document_edit', objective='仅调整超时。'), AIMessage(content=output)])
    result = run(context(model, monkeypatch, **values), streaming)
    if mode == 'whole':
        result = json.loads(result)['edits'][0]['content']
    assert result == expected
    assert len(model.observed) == 2


@pytest.mark.parametrize('streaming', [False, True])
def test_preserved_report_html_can_be_edited_without_rewriting_table(monkeypatch, streaming):
    source = '<table><tr><th>指标</th><th>值</th></tr><tr><td>超时</td><td>30秒</td></tr></table>'
    model = ObservedModel(responses=[call('prepare_document_edit', objective='仅调整超时。'),
        AIMessage(content=json.dumps({'replacements': [{'before': '<td>30秒</td>', 'after': '<td>60秒</td>'}]}))])
    result = run(context(model, monkeypatch, existing=source, full_markdown='# 目标\n\n'+source, edit_in_place=True), streaming)
    assert result == source.replace('30秒', '60秒')
    assert len(model.observed) == 2


@pytest.mark.parametrize('streaming', [False, True])
def test_html_introduced_by_replacement_receives_format_correction(monkeypatch, streaming):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='改写正文。'),
        AIMessage(content=json.dumps({'replacements': [{'before': '原文', 'after': '<p>内容</p>'}]})),
        AIMessage(content=json.dumps({'replacements': [{'before': '原文', 'after': '内容'}]}))])
    assert run(context(model, monkeypatch, edit_in_place=True), streaming) == '必须保留的完整内容。'
    assert len(model.observed) == 3


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('wrapped', [
    '{"content":"正确正文。"}',
    '```json\n{"content":"正确正文。"}\n```',
    '{"replacements":[{"before":"必须保留的完整原文。","after":"正确正文。"}]}',
])
def test_prose_mode_decodes_known_protocol_without_exposing_json(monkeypatch, streaming, wrapped):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='编写正文。'), AIMessage(content=wrapped)])
    assert run(context(model, monkeypatch), streaming) == '正确正文。'
    assert len(model.observed) == 2


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('wrapped', ['{"fields":["不能直接写入正文"]}', '[{"content":"不能擅自拼接"}]', '{"content":42}'])
def test_unknown_data_protocol_is_corrected_once(monkeypatch, streaming, wrapped):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='编写正文。'), AIMessage(content=wrapped),
                                    AIMessage(content='正确正文。')])
    assert run(context(model, monkeypatch), streaming) == '正确正文。'
    assert len(model.observed) == 3


def test_structured_content_still_requires_body_validation(monkeypatch):
    model = ObservedModel(responses=[call('prepare_document_edit', objective='编写。'),
                                    AIMessage(content='{"content":"<script>run()</script>"}'),
                                    AIMessage(content='{"content":"<script>run()</script>"}')])
    with pytest.raises(agent.DraftFormatError):
        run(context(model, monkeypatch), False)
    assert len(model.observed) == 3
