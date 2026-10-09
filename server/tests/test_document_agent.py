"""The agent must finish reading and planning before document prose is released."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import app.document_agent as module


class ObservedModel(FakeMessagesListChatModel):
    """Script tool calls while recording the actual model inputs and tool grants."""

    observed: list[tuple[list, list[str]]] = Field(default_factory=list)
    summaries: int = 0

    def bind_tools(self, tools, **kwargs):
        return self.bind(test_tools=[item.name if hasattr(item, "name") else item["function"]["name"]
                                     for item in tools])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed.append((messages, kwargs.get("test_tools", [])))
        # Summarization invokes the same model with one human summary prompt.
        # Keep that auxiliary call independent of the scripted planning turns.
        if len(messages) == 1 and isinstance(messages[0], HumanMessage) and "<messages>" in str(messages[0].content):
            self.summaries += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="历史摘要：保持术语一致。"))])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": str(uuid4()), "type": "tool_call"}])


@pytest.fixture(autouse=True)
def offline_context(monkeypatch):
    # Never read project details, uploaded Word templates, or business memories.
    monkeypatch.setattr(module, "_project_overview", lambda project_id: "项目名称：中性规划测试")
    monkeypatch.setattr(module, "_skill_files", lambda: {})
    monkeypatch.setattr(module, "load_task_memory", lambda document_id, **kwargs: "已采纳要求：术语保持一致。")
    monkeypatch.setattr(module, "read_memory_history", lambda document_id, **kwargs: [])


def context(model, monkeypatch, **kwargs):
    monkeypatch.setattr(module, "ChatOpenAI", lambda **settings: model)
    values = dict(project_id=uuid4(), document_id=uuid4(), heading="目标", level=1,
                  existing="必须保留的完整原文。", full_markdown="# 目标\n\n必须保留的完整原文。",
                  heading_index=0, instruction="仅润色本节，不扩大范围。", base_url="http://localhost/v1",
                  model="offline-test", api_key="", source_revision=17)
    values.update(kwargs)
    return values


def system_text(messages):
    return "\n".join(str(item.content) for item in messages if isinstance(item, SystemMessage))


def test_prepared_section_generates_prose_and_final_model_has_no_tools(monkeypatch):
    model = ObservedModel(responses=[call("prepare_document_edit", objective="润色目标章节。"),
                                     AIMessage(content="保留事实后的本节正文。")])
    values = context(model, monkeypatch)
    assert module.draft_section_text(**values) == "保留事实后的本节正文。"
    writing = model.observed[-1]
    assert writing[1] == []
    assert "必须保留的完整原文。" in system_text(writing[0])
    assert "仅润色本节，不扩大范围。" in system_text(model.observed[0][0])
    assert "中性规划测试" in system_text(model.observed[0][0])
    assert '"sourceRevision": 17' in system_text(writing[0])


@pytest.mark.parametrize("scope", ["section", "selection", "document"])
def test_template_guidance_is_scoped_and_survives_preparation(monkeypatch, scope):
    calls = [call("read_document_sections")] if scope == "document" else []
    calls += [call("prepare_document_edit", objective="按目标模板要求润色。", **({"sections": [1]} if scope == "document" else {})),
              AIMessage(content="保留事实后的本节正文。")]
    model = ObservedModel(responses=calls)
    blocks = [
        {"id": "root", "kind": "heading", "headingLevel": 1, "sourceText": "总则", "headingId": None},
        {"id": "root-guide", "kind": "guidance", "text": "总则约束必须保留。", "headingId": "root"},
        {"id": "target", "kind": "heading", "headingLevel": 2, "sourceText": "目标", "headingId": None},
        {"id": "target-guide", "kind": "guidance", "text": "目标指导明确要求真实依据。", "headingId": "target"},
        {"id": "sample", "kind": "example", "text": "示例应作为参考，不作项目事实。", "headingId": "target"},
        {"id": "other", "kind": "heading", "headingLevel": 2, "sourceText": "其他", "headingId": None},
        {"id": "other-guide", "kind": "guidance", "text": "无关章节独有的指导。", "headingId": "other"},
    ]
    values = context(model, monkeypatch, full_markdown="# 总则\n\n## 目标\n\n必须保留的完整原文。\n\n## 其他\n\n其他正文。",
                     heading_index=None if scope == "document" else 1, level=2,
                     selected_only=scope == "selection", whole_document=scope == "document",
                     template_structure={"reviewed": True, "blocks": blocks})
    assert module.draft_section_text(**values) == "保留事实后的本节正文。"
    for messages, _ in (model.observed[-1:] if scope == "document" else model.observed):
        pinned = system_text(messages)
        assert "目标指导明确要求真实依据" in pinned
        assert "总则约束必须保留" in pinned
        assert "示例应作为参考" in pinned
        assert "无关章节独有的指导" not in pinned
        assert "仅润色本节，不扩大范围。" in pinned


@pytest.mark.parametrize("whole", [False, True])
def test_new_heading_prompt_inheritance_notice_reaches_public_progress(monkeypatch, whole):
    responses = [call("read_document_sections")] if whole else []
    responses += [call("prepare_document_edit", objective="编写新增功能。", **({"sections": [2]} if whole else {})),
                  AIMessage(content="正式新增功能正文。")]
    model = ObservedModel(responses=responses)
    blocks = [{"id": "root", "kind": "heading", "headingLevel": 1, "sourceText": "总则", "headingId": None},
              {"id": "root-guide", "kind": "guidance", "text": "上级明确提供的编写提示词。", "headingId": "root"},
              {"id": "target", "kind": "heading", "headingLevel": 2, "sourceText": "目标", "headingId": None},
              {"id": "target-guide", "kind": "guidance", "text": "本模块明确提供的提示词。", "headingId": "target"}]
    values = context(model, monkeypatch, full_markdown="# 总则\n\n## 目标\n\n### 新增功能\n\n原始功能内容。",
                     heading="全文" if whole else "新增功能", heading_index=None if whole else 2,
                     level=0 if whole else 3, whole_document=whole,
                     template_structure={"reviewed": True, "blocks": blocks})

    async def collect():
        return [value async for value in module.stream_section_text(**values, include_progress=True)]

    output = asyncio.run(collect())
    notices = [value for value in output if isinstance(value, dict) and value["stage"] == "template"]
    assert len(notices) == 1 and "仅继承已匹配上级" in notices[0]["message"]
    assert "本模块明确提供的提示词" in system_text(model.observed[-1][0])
    assert "上级明确提供的编写提示词" in system_text(model.observed[-1][0])


def test_direct_unprepared_answer_is_rejected(monkeypatch):
    model = ObservedModel(responses=[AIMessage(content="绕过读取与规划的正文。")])
    with pytest.raises(ValueError, match="准备|规划|计划"):
        module.draft_section_text(**context(model, monkeypatch))
    assert len(model.observed) == 3  # Two correction reminders, then a bounded rejection.
    assert all(not any(isinstance(item, AIMessage) and item.content == "绕过读取与规划的正文。" for item in messages)
               for messages, _ in model.observed[1:])


def test_task_memory_uses_current_scope_and_target(monkeypatch):
    requested = []
    monkeypatch.setattr(module, "load_task_memory", lambda document_id, **kwargs: requested.append(kwargs) or "")
    for extra, scope, target in (({}, "section", "目标"), ({"selected_only": True}, "selection", "目标"),
                                 ({"whole_document": True, "heading": "整篇文档", "heading_index": None}, "document", "整篇文档")):
        values = context(ObservedModel(responses=[]), monkeypatch, **extra)
        module._section_agent(**values)
        assert requested[-1]["scope"] == scope and requested[-1]["target"] == target
        assert requested[-1]["instruction"] == values["instruction"]


def test_whole_document_prepare_requires_every_nonempty_section_and_valid_plan(monkeypatch):
    responses = [
        call("prepare_document_edit", objective="只润色甲节。", sections=[0]),
        call("read_document_section", index=0),
        call("prepare_document_edit", objective="只润色甲节。", sections=[0]),
        call("read_document_section", index=1),
        call("read_document_section", index=-1),
        call("prepare_document_edit", objective="错误范围。", sections=[99]),
        call("prepare_document_edit", objective="只润色甲节。", sections=[0]),
        AIMessage(content='{"edits":[{"headingIndex":0,"content":"甲节修改稿。"}]}'),
    ]
    model = ObservedModel(responses=responses)
    values = context(model, monkeypatch, heading="整篇文档", heading_index=None, whole_document=True,
                     existing="", full_markdown="封面正文。\n\n# 甲\n\n甲节完整原文。\n\n# 乙\n\n乙节保持不变。")
    agent, inputs, prepared = module._section_agent(**values)
    result = agent.invoke(inputs, config={"recursion_limit": 80})
    assert prepared["ready"] is True
    messages = result["messages"]
    planning_results = [item for item in messages if isinstance(item, ToolMessage) and item.name == "prepare_document_edit"]
    assert len(planning_results) == 4
    assert all("error" in json.loads(item.content) for item in planning_results[:3])
    assert json.loads(planning_results[-1].content)["ready"] is True
    final_system = system_text(model.observed[-1][0])
    assert "甲节完整原文。" in final_system
    assert "乙节保持不变。" not in final_system
    assert model.observed[-1][1] == []
    assert result["messages"][-1].content == responses[-1].content


@pytest.mark.parametrize("selected_only,sections", [(False, [1]), (True, [0])])
def test_section_and_selection_plan_cannot_expand_edit_scope(monkeypatch, selected_only, sections):
    model = ObservedModel(responses=[call("prepare_document_edit", objective="扩大范围。", sections=sections),
                                     call("prepare_document_edit", objective="只修改当前范围。"),
                                     AIMessage(content="当前范围的正文。")])
    values = context(model, monkeypatch, selected_only=selected_only,
                     full_markdown="# 目标\n\n必须保留的完整原文。\n\n# 其他\n\n不能改写。")
    agent, inputs, prepared = module._section_agent(**values)
    result = agent.invoke(inputs, config={"recursion_limit": 32})
    results = [item for item in result["messages"] if isinstance(item, ToolMessage) and item.name == "prepare_document_edit"]
    assert len(results) == 2 and "error" in json.loads(results[0].content)
    assert prepared["ready"] is True and model.observed[-1][1] == []


def test_whole_document_must_read_contiguous_pages_before_preparing(monkeypatch):
    original = "分页完整原文。" * 2000
    model = ObservedModel(responses=[
        call("read_document_section", index=0, offset=12000),
        call("prepare_document_edit", objective="保留完整原文。", sections=[0]),
        call("read_document_section", index=0),
        call("prepare_document_edit", objective="保留完整原文。", sections=[0]),
        call("read_document_section", index=0, offset=12000),
        call("prepare_document_edit", objective="保留完整原文。", sections=[0]),
        AIMessage(content='{"edits":[{"headingIndex":0,"content":"已生成草稿。"}]}'),
    ])
    read, planned = set(), set()
    values = context(model, monkeypatch, heading="整篇文档", heading_index=None, whole_document=True,
                     existing="", full_markdown="# 甲\n\n" + original + "\n\n# 空节\n",
                     context_tokens=64000, read_sections=read, planned_sections=planned)
    agent, inputs, prepared = module._section_agent(**values)
    result = agent.invoke(inputs, config={"recursion_limit": 80})
    planning_results = [item for item in result["messages"] if isinstance(item, ToolMessage) and item.name == "prepare_document_edit"]
    assert all("error" in json.loads(item.content) for item in planning_results[:2])
    assert prepared["ready"] is True and planned == {0} and 0 in read
    assert json.loads(prepared["source"])["sections"][0]["content"] == original


@pytest.mark.parametrize("name,args", [("write_file", {"file_path": "/draft.md", "content": "不应写入。"}),
                                        ("task", {"description": "不应执行子智能体。", "subagent_type": "general-purpose"})])
def test_agent_rejects_write_and_subagent_operations_even_if_model_requests_them(monkeypatch, name, args):
    model = ObservedModel(responses=[call(name, **args)])
    with pytest.raises(module.DraftFormatError, match="不能执行其他操作"):
        module.draft_section_text(**context(model, monkeypatch))
    assert name not in model.observed[0][1]


def test_real_agent_stream_releases_only_prepared_final_answer(monkeypatch):
    preparing = call("prepare_document_edit", objective="只润色本节。")
    preparing.content = "先规划正文编写。"
    model = ObservedModel(responses=[call("read_document_section", index=0), preparing, AIMessage(content="最终文档正文。")])
    values = context(model, monkeypatch)

    async def collect():
        return [value async for value in module.stream_section_text(**values)]

    output = asyncio.run(collect())
    assert output and output[-1] == "最终文档正文。"
    assert all("规划" not in value for value in output)
    assert model.observed[-1][1] == []


def test_unprepared_stream_rejects_answer_without_yielding_it(monkeypatch):
    model = ObservedModel(responses=[AIMessage(content="绕过编写准备的直答。")])
    values, output = context(model, monkeypatch), []

    async def collect():
        async for value in module.stream_section_text(**values):
            output.append(value)

    with pytest.raises(module.DraftFormatError, match="编写准备"):
        asyncio.run(collect())
    assert output == []
    assert len(model.observed) == 3


@pytest.mark.parametrize("streaming", [False, True])
def test_premature_answer_is_removed_and_corrected_before_reading_and_writing(monkeypatch, streaming):
    premature = "未准备的虚构正文不得作为后续事实。"
    model = ObservedModel(responses=[AIMessage(content=premature), call("read_document_section", index=0),
                                     call("prepare_document_edit", objective="根据完整原文润色。"),
                                     AIMessage(content="经过准备的最终正文。")])
    values = context(model, monkeypatch)
    if streaming:
        async def collect():
            return [value async for value in module.stream_section_text(**values)]
        output = asyncio.run(collect())
        assert output and output[-1] == "经过准备的最终正文。"
        assert all(premature not in value for value in output)
    else:
        assert module.draft_section_text(**values) == "经过准备的最终正文。"
    assert len(model.observed) == 4
    assert all(premature not in str(item.content) for messages, _ in model.observed[1:] for item in messages)
    assert any("现在请调用工具完成准备" in str(item.content) for item in model.observed[1][0])
    assert model.observed[-1][1] == []


def test_public_sync_allows_legal_read_chain_within_model_and_tool_limits(monkeypatch):
    model = ObservedModel(responses=[*[call("search_writing_memory", query=f"约束 {index}") for index in range(5)],
                                     call("prepare_document_edit", objective="按相关约束润色本节。"),
                                     AIMessage(content="按约束完成的正文。")])
    assert module.draft_section_text(**context(model, monkeypatch)) == "按约束完成的正文。"


def test_read_and_prepare_same_turn_returns_recoverable_error(monkeypatch):
    combined = call("read_document_section", index=0)
    combined.tool_calls += call("prepare_document_edit", objective="润色当前章节。").tool_calls
    model = ObservedModel(responses=[combined, call("prepare_document_edit", objective="润色当前章节。"),
                                     AIMessage(content="准备完成后的正文。")])
    agent, inputs, prepared = module._section_agent(**context(model, monkeypatch))
    result = agent.invoke(inputs, config={"recursion_limit": 32})
    attempts = [item for item in result["messages"] if isinstance(item, ToolMessage) and item.name == "prepare_document_edit"]
    assert len(attempts) == 2 and "error" in json.loads(attempts[0].content)
    assert "单独" in attempts[0].content and json.loads(attempts[1].content)["ready"] is True
    assert any(isinstance(item, ToolMessage) and item.name == "read_document_section" for item in result["messages"])
    assert prepared["ready"] is True and model.observed[-1][1] == []
    assert result["messages"][-1].content == "准备完成后的正文。"


def test_twelve_thousand_character_chapter_fits_32k_without_duplicate_source(monkeypatch):
    original = "原文起点" + "保留内容" * 2998 + "原文终点"
    assert len(original) == 12000
    model = ObservedModel(responses=[call("read_document_section", index=0),
                                     call("prepare_document_edit", objective="保留完整事实并润色。"),
                                     AIMessage(content="符合要求的正文。")])
    values = context(model, monkeypatch, existing=original, full_markdown="# 目标\n\n" + original,
                     context_tokens=32000)
    assert module.draft_section_text(**values) == "符合要求的正文。"
    messages, tools = model.observed[-1]
    assert tools == [] and original in system_text(messages)
    assert all(original not in str(item.content) for item in messages if isinstance(item, (HumanMessage, ToolMessage)))
    assert sum(str(item.content).count(original) for item in messages) == 1
    # Original reads remain in the preparation history and the final source is
    # complete, so reduced final inputs are deduplication rather than truncation.
    assert any(isinstance(item, ToolMessage) and original in item.content
               for messages, _ in model.observed[:-1] for item in messages)


def test_over_budget_full_source_is_rejected_before_final_model(monkeypatch):
    original = "完整起点" + "不能静默删除" * 3000 + "完整终点"
    model = ObservedModel(responses=[call("prepare_document_edit", objective="完整保留原始正文。"),
                                     AIMessage(content="不应请求最终模型。")])
    values = context(model, monkeypatch, existing=original, full_markdown="# 目标\n\n" + original,
                     context_tokens=8000)
    agent, inputs, prepared = module._section_agent(**values)
    with pytest.raises(module.DraftFormatError, match="超过模型容量"):
        agent.invoke(inputs, config={"recursion_limit": 32})
    assert prepared["ready"] is True
    assert json.loads(prepared["source"])["sections"][0]["content"] == original
    actual_turns = [item for item in model.observed if any(isinstance(message, SystemMessage) for message in item[0])]
    assert len(actual_turns) == 1  # Budget guard runs before requesting final prose.


def test_native_compaction_keeps_fixed_task_and_offloads_raw_history(monkeypatch):
    model = ObservedModel(responses=[call("search_writing_memory", query="目标"),
                                     call("search_writing_memory", query="术语"),
                                     call("search_writing_memory", query="约束"),
                                     call("search_writing_memory", query="早期"),
                                     call("search_writing_memory", query="版本"),
                                     call("prepare_document_edit", objective="只润色目标章节。"),
                                     AIMessage(content="本次正文。")])
    monkeypatch.setattr(module, "read_memory_history", lambda document_id, **kwargs: [
        {"instruction": "历史要求完整原文。" * 180, "target": "目标", "scope": "section", "revision": 1}
    ])
    values = context(model, monkeypatch, context_tokens=12000)
    agent, inputs, prepared = module._section_agent(**values)
    task = inputs["messages"][0]
    inputs["messages"] = [message for index in range(6) for message in (
        HumanMessage(content=f"原始要求-{index}：" + "历史正文。" * 1200), AIMessage(content="历史答复。" * 1200),
    )] + [task]
    result = agent.invoke(inputs, config={"recursion_limit": 64})
    assert prepared["ready"] is True and result["messages"][-1].content == "本次正文。"
    assert model.summaries >= 2  # Both old history and later tool results were compressed.
    actual_turns = [item for item in model.observed if any(isinstance(message, SystemMessage) for message in item[0])]
    assert all("仅润色本节，不扩大范围。" in system_text(messages) for messages, _ in actual_turns)
    assert "必须保留的完整原文。" in system_text(actual_turns[-1][0])
    assert actual_turns[-1][1] == []
    history = [file["content"] for name, file in result["files"].items() if name.startswith("/conversation_history/")]
    assert history and any("原始要求-0" in value for value in history)
    assert "已采纳要求：术语保持一致。" in system_text(actual_turns[-1][0])


def test_stream_excludes_preparation_prose_tool_calls_and_summary_output(monkeypatch):
    prepared = {"ready": False}

    class EventAgent:
        async def astream(self, inputs, **kwargs):
            metadata = {"langgraph_node": "model"}
            yield "messages", (AIMessageChunk(content="先分析当前章节。", id="plan"), metadata)
            yield "updates", {"model": {"messages": [AIMessage(content="尚未准备的直答。", id="premature")]}}
            yield "messages", (AIMessageChunk(content="调用读取工具。", id="tools",
                                                tool_call_chunks=[{"name": "prepare_document_edit", "args": "{}", "id": "p", "index": 0}]), metadata)
            prepared["ready"] = True
            yield "messages", (AIMessageChunk(content="不应进入正文的摘要。", id="summary"),
                                {**metadata, "lc_source": "summarization"})
            yield "messages", (AIMessageChunk(content="真正", id="draft"), metadata)
            yield "messages", (AIMessageChunk(content="正文。", id="draft"), metadata)
            yield "updates", {"model": {"messages": [AIMessage(content="真正正文。", id="draft")]}}

    monkeypatch.setattr(module, "_section_agent", lambda **kwargs: (EventAgent(), {}, prepared))

    async def collect():
        return [value async for value in module.stream_section_text()]

    assert asyncio.run(collect()) == ["真正", "真正正文。"]


def test_stream_progress_reports_real_operations_without_exposing_reasoning(monkeypatch):
    preparing = call("prepare_document_edit", objective="私密编写目标不应显示。", sections=[0])
    preparing.content = "私密准备分析不应显示。"
    model = ObservedModel(responses=[call("read_document_sections"),
                                     call("search_writing_memory", query="私密查询不应显示。"),
                                     preparing, AIMessage(content="最终正文。")])
    values = context(model, monkeypatch, heading="全文", heading_index=None, whole_document=True,
                     existing="", read_sections=set(), planned_sections=set())

    async def collect():
        return [value async for value in module.stream_section_text(**values, include_progress=True)]

    output = asyncio.run(collect())
    statuses = [value for value in output if isinstance(value, dict)]
    assert all(value["type"] == "progress" and set(value) == {"type", "stage", "message"} for value in statuses)
    assert {"context", "planning", "reading", "memory", "writing"}.issubset({value["stage"] for value in statuses})
    assert any(value["message"] == "已完整读取 1 个章节" for value in statuses)
    assert statuses[-1] == {"type": "progress", "stage": "writing", "message": "正在生成正文"}
    assert output[-1] == "最终正文。" and "私密" not in json.dumps(output, ensure_ascii=False)


@pytest.mark.parametrize("batch", [False, True])
def test_hundred_nonempty_chapters_can_be_fully_read_before_one_chapter_edit(monkeypatch, batch):
    reads = (call("read_document_sections") if batch else AIMessage(content="", tool_calls=[
        {"name": "read_document_section", "args": {"index": index}, "id": f"chapter-{index}", "type": "tool_call"}
        for index in range(100)
    ]))
    answer = '{"edits":[{"headingIndex":0,"content":"只润色第一章的正文。"}]}'
    model = ObservedModel(responses=[reads, call("prepare_document_edit", objective="只润色第一章。", sections=[0]),
                                     AIMessage(content=answer)])
    read, planned = set(), set()
    values = context(model, monkeypatch, heading="全文", heading_index=None, whole_document=True, existing="",
                     full_markdown="\n\n".join(f"# 第{index}章\n\n原文。" for index in range(100)),
                     read_sections=read, planned_sections=planned)
    assert module.draft_section_text(**values) == answer
    assert set(range(100)).issubset(read) and planned == {0}
    assert len([turn for turn in model.observed if turn[1]]) == 2


def test_batch_reader_pages_exact_original_without_counting_skipped_text(monkeypatch):
    original = '原文 "引号"\n下一行。' * 1400
    model = ObservedModel(responses=[AIMessage(content="unused")])
    read = set()
    values = context(model, monkeypatch, heading="全文", heading_index=None, whole_document=True, existing="",
                     full_markdown="# 长章\n\n" + original + "\n\n# 尾章\n\n尾部原文。", read_sections=read)
    captured = {}

    def capture_agent(**kwargs):
        captured.update({item.name: item for item in kwargs["tools"]})
        return object()

    monkeypatch.setattr(module, "create_deep_agent", capture_agent)
    module._section_agent(**values)
    reader = captured["read_document_sections"]
    skipped = json.loads(reader.invoke({"start_index": 0, "offset": 12000}))
    assert skipped["sections"][0]["offset"] == 12000 and 0 not in read
    start, offset, reconstructed, pages = -1, 0, {}, 0
    while True:
        raw = reader.invoke({"start_index": start, "offset": offset, "limit": 1000})
        assert len(raw) <= 1000
        page = json.loads(raw)
        for section in page["sections"]:
            reconstructed.setdefault(section["index"], "")
            assert section["offset"] == len(reconstructed[section["index"]])
            reconstructed[section["index"]] += section["content"]
        pages += 1
        if not page["hasMore"]:
            break
        start, offset = page["nextIndex"], page["nextOffset"]
        assert pages < 100
    assert reconstructed[0] == original and reconstructed[1] == "尾部原文。"
    assert {0, 1}.issubset(read) and pages > 1


def test_final_preparation_deduplicates_batch_source_without_dropping_other_chapters(monkeypatch):
    original = "完整原文起点。" + "事实保留。" * 2450 + "完整原文终点。"
    # At a 12,000-character page budget this chapter spans two batch reads.
    model = ObservedModel(responses=[call("read_document_sections"), call("read_document_sections", start_index=0, offset=11625),
                                     call("prepare_document_edit", objective="保留事实，仅润色长章。", sections=[0]),
                                     AIMessage(content='{"edits":[{"headingIndex":0,"content":"修改稿。"}]}')])
    values = context(model, monkeypatch, heading="全文", heading_index=None, whole_document=True, existing="",
                     full_markdown="# 长章\n\n" + original + "\n\n# 其他\n\n其他章节的事实。", context_tokens=32000)
    # Use the emitted continuation pointer rather than assuming JSON overhead.
    real_call = model._generate

    def adapt_generate(messages, **kwargs):
        if model.i == 1:
            prior = next(item for item in reversed(messages) if isinstance(item, ToolMessage) and item.name == "read_document_sections")
            continuation = json.loads(prior.content)
            model.responses[1].tool_calls[0]["args"] = {"start_index": continuation["nextIndex"], "offset": continuation["nextOffset"]}
        return real_call(messages, **kwargs)

    monkeypatch.setattr(model, "_generate", adapt_generate)
    module.draft_section_text(**values)
    messages = model.observed[-1][0]
    assert original in system_text(messages)
    assert all(original not in str(item.content) for item in messages if isinstance(item, ToolMessage))
    assert any("其他章节的事实。" in str(item.content) for item in messages if isinstance(item, ToolMessage))
