"""Draft one document section with a project-scoped DeepAgents agent."""

from __future__ import annotations

from uuid import UUID
from collections.abc import AsyncIterator
from copy import deepcopy
import json
import re
from functools import partial

from deepagents import create_deep_agent
from deepagents.backends.utils import create_file_data
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.tools import tool
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, ToolCallLimitMiddleware, hook_config
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_openai import ChatOpenAI

from app.db import connect
from app.config import ai_reasoning_kwargs
from app.docx_parser import parse_docx
from app.document_rules import SKILL_ROOT, DraftFormatError, section_rules
from app.document_memory import document_context, load_task_memory, read_memory_history
from app.template_import import guidance_for_section


def _project_overview(project_id: UUID) -> str:
    with connect() as connection:
        row = connection.execute(
            """SELECT p.name, p.description, p.organization, p.department,
                      p.group_name, p.client_contact, p.status, u.name AS owner_name
               FROM projects p JOIN users u ON u.id = p.owner_id WHERE p.id = %s""",
            (project_id,),
        ).fetchone()
    if row is None:
        return "项目概述不可用。"
    fields = (("项目名称", "name"), ("负责人", "owner_name"), ("甲方单位", "organization"),
              ("甲方联系人", "client_contact"), ("所属部门", "department"),
              ("所属组", "group_name"), ("状态", "status"), ("项目说明", "description"))
    return "\n".join(f"{label}：{row[key]}" for label, key in fields if row[key])


class _WritingPreparation(AgentMiddleware):
    """Pin the current task outside summarized history and restrict execution to reading."""

    allowed_tools = {"read_project_overview", "read_document_section", "read_document_sections", "search_writing_memory",
                     "prepare_document_edit", "read_file", "ls", "glob", "grep"}

    def __init__(self, task: str, memory: str, preparation: dict, context_tokens: int,
                 initial_content: str, background: str):
        self.task, self.memory, self.preparation = task, memory, preparation
        self.input_budget = int(context_tokens * 0.75)
        self.initial_content, self.background = initial_content, background

    def _request(self, request):
        original = request.system_message.text if request.system_message else ""
        pinned = f"\n\n本次固定任务（历史压缩不能替代本次要求）：\n{self.task}\n\n相关已采纳要求：\n{self.memory}"
        if self.preparation["ready"]:
            pinned += "\n\n编写准备已完成。仅输出最终正文，不再调用工具。修改范围与完整原文：\n" + self.preparation["source"]
        system = SystemMessage(content=original + pinned)
        tools = [] if self.preparation["ready"] else [
            item for item in request.tools
            if (item.get("name") if isinstance(item, dict) else item.name) in self.allowed_tools
        ]
        messages = list(request.messages)
        if self.preparation["ready"]:
            # The final call receives one complete copy of modified source. Keep
            # tool-call pairs, skill reads and unrelated source intact, but remove
            # exact duplicates already pinned above rather than truncating source.
            for index, message in enumerate(messages):
                if isinstance(message, HumanMessage) and message.content == self.initial_content:
                    messages[index] = message.model_copy(update={"content": "当前任务见固定任务，修改原文见编写准备。文档背景：\n" + self.background})
                elif isinstance(message, ToolMessage) and message.name == "read_document_section":
                    try:
                        section = json.loads(message.content)
                        if section["index"] in self.preparation["sections"]:
                            messages[index] = message.model_copy(update={"content": "该节完整原文、约束与记忆已保留在编写准备中。"})
                    except (ValueError, KeyError, TypeError):
                        pass
                elif isinstance(message, ToolMessage) and message.name == "read_document_sections":
                    try:
                        page = json.loads(message.content)
                        for section in page["sections"]:
                            if section["index"] in self.preparation["sections"]:
                                section["content"] = "该节完整原文已保留在编写准备中。"
                        messages[index] = message.model_copy(update={"content": json.dumps(page, ensure_ascii=False)})
                    except (ValueError, KeyError, TypeError):
                        pass
        updated = request.override(system_message=system, messages=messages, tools=tools, tool_choice=None)
        if self.preparation["ready"]:
            messages = [system, *updated.messages]
            # English is roughly four characters/token; Chinese is closer to one.
            estimate = count_tokens_approximately(messages, chars_per_token=4.0)
            estimate += sum(len(re.findall(r"[\u3400-\u9fff]", message.text)) * 0.75 for message in messages)
            if estimate > self.input_budget:
                raise DraftFormatError("本次修改所需原文与上下文超过模型容量，请缩小修改范围或提高 AI_CONTEXT_TOKENS。")
        return updated

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        message = state["messages"][-1]
        if self.preparation["ready"] or not isinstance(message, AIMessage) or message.tool_calls:
            return None
        # Some thinking models do not support tool_choice='required'. Correct a
        # premature answer inside the graph, with a bounded number of retries.
        if self.preparation["reminders"] >= 2:
            raise DraftFormatError("AI 尚未完成编写准备，请重新生成。")
        self.preparation["reminders"] += 1
        updates = [RemoveMessage(id=message.id)] if message.id else []
        updates.append(HumanMessage(content=(
            "上一回答未通过编写准备检查，已丢弃，不能作为正文或事实依据。"
            "请读取本次要求所需的原文与技能；全文模式须读完全部非空章节。"
            "完成读取后，单独调用prepare_document_edit声明目标与修改章节；"
            "成功后下一轮才输出最终正文。现在请调用工具完成准备。"
        )))
        return {"messages": updates, "jump_to": "model"}

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)

    def _check_tool(self, request):
        if self.preparation["ready"] or request.tool_call["name"] not in self.allowed_tools:
            raise DraftFormatError("编写智能体只能读取资料并生成草稿，不能执行其他操作。")

    def wrap_tool_call(self, request, handler):
        self._check_tool(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._check_tool(request)
        return await handler(request)


def _without_examples(value):
    """Keep template rules; sample projects are not evidence about the user's project."""
    if isinstance(value, dict):
        return {key: "" if key == "content" and not isinstance(item, (dict, list)) else [] if key == "rows" else {} if key == "subsections"
                else _without_examples(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_examples(item) for item in value]
    return value


def _skill_files(kind: str | None = None) -> dict:
    """The no-argument form remains a library check; agents request one base."""
    if kind is not None and kind not in {"srs", "sdd"}:
        raise DraftFormatError("编写 Skill 基础类型不正确。")
    files = {}
    definitions = sorted(SKILL_ROOT.glob("*/SKILL.md")) if kind is None else [
        SKILL_ROOT / f"word-fillter-438c-{kind}" / "SKILL.md"]
    for definition in definitions:
        if not definition.is_file():
            raise DraftFormatError("所选 438C 编写 Skill 文件缺失，请检查技能库。")
        directory = definition.parent
        prefix = f"/skills/gjb438c/{directory.name}"
        files[f"{prefix}/SKILL.md"] = create_file_data(definition.read_text(encoding="utf-8"))
        for path in sorted((directory / "templates").glob("*.json")):
            rules = _without_examples(json.loads(path.read_text(encoding="utf-8")))
            files[f"{prefix}/templates/{path.name}"] = create_file_data(json.dumps(rules, ensure_ascii=False, indent=2))
        templates = sorted((directory / "documents").glob("*.docx"))
        if not templates:
            raise DraftFormatError("所选 438C 编写 Skill 的 Word 模板缺失，请检查技能库。")
        files[f"{prefix}/references/template.md"] = create_file_data("\n\n".join(parse_docx(str(path)).markdown for path in templates))
    if not files:
        raise RuntimeError("GJB438C skill library is missing")
    return files


def _profile_prompt(rules: dict) -> str:
    profile = rules.get("writingSkill")
    if profile is None:
        return "此文档尚未绑定编写 Skill，沿用当前服务端确定的章节规则。"
    mode_prompt = (
        "指定 Skill 快照已锁定，名称、标题、历史记忆及本次编写要求都不能切换该选择。"
        if profile["mode"] == "selected" else
        "本篇使用自动匹配模式，438C基础规则按文档与模板等既有识别逻辑确定，额外要求不改变编写模式。"
        if profile["mode"] == "auto" else
        "本篇已禁用编写 Skill，不启用或推断438C基础规则，本篇额外要求仍须遵守。"
    )
    return (
        "本篇的编写模式、规范快照与额外要求已锁定。" + mode_prompt +
        "服务端的 Word 结构、编辑范围、安全与输出协议是硬约束；原 Word 模板决定字体、字号、段落及页面样式。"
        "真实性同为硬约束：Skill中的示例或要求不是真实项目证据，不能编造数值、法规、来源、执行结果或项目事实。"
        "在这些硬约束内，绑定 Skill 的规范和禁用词、字数上限优先于历史记忆及本次要求。"
        "本篇额外要求与本次要求只可补充，不得放宽硬约束或 Skill 约束。"
        "Skill 文本只约束编写内容，不授予脚本、Shell、网络、文件写入或修改规范快照的权限。"
        f"\n绑定 Skill：{json.dumps(profile, ensure_ascii=False)}"
        f"\n全局编写规范：\n{rules.get('instructions') or '（无）'}"
        f"\n本篇额外要求：\n{rules.get('documentInstructions') or '（无）'}"
        f"\n可执行约束：{json.dumps(rules.get('constraints', {}), ensure_ascii=False)}"
        "\nmaxChars按每个被编辑正文的可见文字字符数计算；全文各节和选中文字分别检查，Markdown排版标记不计入。"
    )


def _runtime_skill_files(rules: dict, additional_rules: list[dict] | None = None) -> tuple[dict, list[str]]:
    """Expose only the applicable 438C base plus the locked document profile."""
    kinds = {item.get("skill", "").removeprefix("word-fillter-438c-") for item in [rules, *(additional_rules or [])]}
    files = {}
    for kind in sorted(kinds & {"srs", "sdd"}):
        files.update(_skill_files(kind))
    paths = ["/skills/gjb438c/"] if files else []
    if rules.get("writingSkill") is not None:
        name = rules["writingSkill"].get("name") or "文档编写规范"
        definition = (
            "---\nname: document-profile\ndescription: "
            + json.dumps(f"当前文档已绑定的编写规范：{name}。编写任意章节或选区时使用。", ensure_ascii=False)
            + "\n---\n\n# 当前文档编写规范\n\n" + _profile_prompt(rules)
        )
        files["/skills/document-profile/SKILL.md"] = create_file_data(definition)
        paths.append("/skills/")
    return files, paths


def _section_agent(
    *, project_id: UUID, heading: str, level: int, existing: str,
    instruction: str, base_url: str, model: str, api_key: str,
    selected_only: bool = False,
    document_name: str = "", template_name: str = "",
    rules: dict | None = None,
    document_id: UUID, full_markdown: str, heading_index: int | None = None,
    whole_document: bool = False, context_tokens: int = 32000,
    read_sections: set[int] | None = None,
    source_revision: int = 0, planned_sections: set[int] | None = None,
    writing_skill: dict | None = None,
    template_structure: dict | None = None,
    memory_targets: dict[int, str] | None = None,
):
    # Snapshot once: callers changing their dictionary after construction cannot
    # change constraints half-way through reading or planning this draft.
    writing_skill = deepcopy(writing_skill)
    template_structure = deepcopy(template_structure)
    rules = deepcopy(rules) if rules is not None and writing_skill is None else section_rules(
        heading=heading, document_name=document_name, template_name=template_name,
        instruction=instruction, existing=existing, selected_only=selected_only, writing_skill=writing_skill,
    )
    scope = "document" if whole_document else "selection" if selected_only else "section"
    instruction = instruction.strip() or (
        "改写选中文字，使表达清晰，保持原意和事实。" if selected_only else
        "通读全文，统一表达并保留原有事实、表格和章节。" if whole_document else
        "根据项目概述和标题编写本节正文，保留已知事实。"
    )
    overview = _project_overview(project_id)
    def template_guidance(index):
        try:
            return guidance_for_section(template_structure, full_markdown, index)
        except ValueError as error:
            raise DraftFormatError(str(error)) from None

    initial_guidance = template_guidance(heading_index)
    preparation = {"ready": False, "source": "", "sections": [], "reminders": 0,
                   "templateNotices": [item["text"] for item in initial_guidance if item["kind"] == "notice"]}
    @tool
    def read_project_overview() -> str:
        """Read the project overview snapshot already loaded for the current task."""
        return overview

    headings = list(re.finditer(r"^(#{1,6})[ \t]+([^\n]+)$", full_markdown, re.MULTILINE))
    read_coverage: dict[int, int] = {}

    def memory_target(index: int | None) -> str:
        if index is None or index < 0:
            return ""
        if memory_targets is not None:
            return memory_targets.get(index, "")
        return headings[index].group(2).strip()[:120]

    def section_text(index: int) -> str:
        start = 0 if index == -1 else headings[index].end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(full_markdown)
        return full_markdown[start:end].strip()

    def record_read(index: int, offset: int, length: int) -> None:
        text = section_text(index)
        if offset <= read_coverage.get(index, 0):
            read_coverage[index] = max(read_coverage.get(index, 0), min(offset + length, len(text)))
        if read_sections is not None and read_coverage.get(index, 0) >= len(text):
            read_sections.add(index)

    @tool
    def read_document_section(index: int, offset: int = 0, limit: int = 12000) -> str:
        """Read this document snapshot's full section. -1 is the pre-heading text. Page with offset when hasMore is true."""
        if index < -1 or index >= len(headings) or offset < 0 or not 1 <= limit <= 12000:
            return "章节或读取范围不正确。"
        text = section_text(index)
        record_read(index, offset, limit)
        chapter_rules = section_rules(heading=headings[index].group(2).strip() if index >= 0 else "正文", document_name=document_name,
                                      template_name=template_name, instruction=instruction, existing=text, writing_skill=writing_skill)
        return json.dumps({"index": index, "content": text[offset:offset + limit],
                           "hasMore": offset + limit < len(text), "nextOffset": offset + limit,
                           "rules": chapter_rules,
                           "templateGuidance": template_guidance(index),
                           "memory": load_task_memory(document_id, scope="section",
                               target=memory_target(index),
                               instruction=instruction, max_chars=2000)}, ensure_ascii=False)

    @tool
    def read_document_sections(start_index: int = -1, offset: int = 0, limit: int = 12000) -> str:
        """Read contiguous sections in one bounded page. Follow nextIndex/nextOffset while hasMore.

        Prefer this tool when reading the whole document. It returns original text,
        not a summary. Rules and relevant memory are pinned during preparation;
        read_document_section remains available for individual chapter rules.
        """
        if (start_index < -1 or start_index >= len(headings) or offset < 0
                or not 1000 <= limit <= 12000 or offset > len(section_text(start_index))):
            return "章节或读取范围不正确。"
        entries, coverage = [], []
        index, position = start_index, offset
        response = {"sections": entries, "hasMore": True, "nextIndex": index, "nextOffset": position}
        while index < len(headings):
            text = section_text(index)
            available = limit - len(json.dumps(response, ensure_ascii=False)) - 256
            if available <= 0:
                break
            chunk = text[position:position + available]
            entry = {"index": index, "heading": headings[index].group(2).strip() if index >= 0 else "正文",
                     "offset": position, "content": chunk}
            # JSON escaping adds bytes for line breaks and quotes. Reduce only
            # this page, and advance coverage by exactly the returned original.
            while True:
                entry["content"] = chunk
                complete = position + len(chunk) >= len(text)
                next_index, next_offset = (index + 1, 0) if complete else (index, position + len(chunk))
                candidate = {"sections": [*entries, entry], "hasMore": next_index < len(headings),
                             "nextIndex": next_index if next_index < len(headings) else None,
                             "nextOffset": next_offset}
                encoded = json.dumps(candidate, ensure_ascii=False)
                if len(encoded) <= limit or not chunk:
                    break
                chunk = chunk[:-max(1, len(encoded) - limit)]
            if len(encoded) > limit or (not chunk and position < len(text)):
                break
            entries.append(entry)
            coverage.append((index, position, len(chunk)))
            response = candidate
            index, position = next_index, next_offset
            if not complete:
                break
        for index, position, length in coverage:
            record_read(index, position, length)
        return json.dumps(response, ensure_ascii=False)

    @tool
    def search_writing_memory(query: str = "", offset: int = 0) -> str:
        """Retrieve original accepted writing requirements for this document only when compressed memory is insufficient."""
        try:
            return json.dumps(read_memory_history(document_id, query=query, offset=offset, limit=5), ensure_ascii=False)
        except ValueError:
            return "记忆查询范围不正确。"

    @tool
    def prepare_document_edit(objective: str, runtime: ToolRuntime, sections: list[int] | None = None) -> str:
        """Finish preparation after reading needed skills/sources. Declare modified section indices (-1 for preface).

        Whole-document mode requires full reads of all nonempty sections first and explicit modified indices.
        Section mode defaults to the selected heading; selection mode uses no section indices.
        Call this tool alone, never alongside reading tools. Success closes all tools;
        the next response must contain only final prose or edits JSON.
        """
        previous = next((message for message in reversed(runtime.state["messages"]) if isinstance(message, AIMessage)), None)
        if previous is None or len(previous.tool_calls) != 1:
            return '{"error":"请先完成读取，再单独调用prepare_document_edit，不要与其他工具同时调用。"}'
        if not objective.strip() or len(objective) > 800:
            return '{"error":"请提供不超过800字的编写目标。"}'
        if selected_only:
            if sections:
                return '{"error":"选区模式只能修改选中文字。"}'
            indices = []
            source = {"selectedText": existing, "rules": rules,
                      "templateGuidance": initial_guidance}
        else:
            indices = sections if sections is not None else ([] if whole_document or heading_index is None else [heading_index])
            if not indices or len(indices) > 200 or len(set(indices)) != len(indices) or any(index < -1 or index >= len(headings) for index in indices):
                return '{"error":"请指定有效且不重复的修改章节索引。"}'
            if not whole_document and indices != [heading_index]:
                return '{"error":"章节模式只能修改当前标题的正文。"}'
            if whole_document:
                required = {index for index in range(-1, len(headings)) if section_text(index)}
                if any(read_coverage.get(index, 0) < len(section_text(index)) for index in required):
                    return '{"error":"请先完整读取所有非空章节，hasMore为true时继续分页读取。"}'
            source = {"sections": [{"headingIndex": index, "heading": headings[index].group(2).strip() if index >= 0 else "正文",
                "content": section_text(index), "rules": section_rules(
                    heading=headings[index].group(2).strip() if index >= 0 else "正文", document_name=document_name,
                    template_name=template_name, instruction=instruction, existing=section_text(index), writing_skill=writing_skill),
                "templateGuidance": template_guidance(index),
                "memory": load_task_memory(document_id, scope="section", target=memory_target(index),
                    instruction=instruction, max_chars=2000)} for index in indices]}
        source["objective"] = objective.strip()
        guidance = source.get("templateGuidance", []) + [item for section in source.get("sections", []) for item in section["templateGuidance"]]
        preparation["templateNotices"] = list(dict.fromkeys(item["text"] for item in guidance if item["kind"] == "notice"))
        preparation.update(ready=True, sections=list(indices), source=json.dumps(source, ensure_ascii=False))
        if planned_sections is not None:
            planned_sections.update(indices)
        return json.dumps({"ready": True, "sections": indices, "next": "仅输出最终正文或edits JSON，不输出计划和解释。"}, ensure_ascii=False)

    chat_model = ChatOpenAI(
        model=model, base_url=base_url, api_key=api_key or "local-model",
        use_responses_api=False, timeout=90, max_retries=1,
        **ai_reasoning_kwargs(),
    )
    backend = StateBackend()
    summarization = SummarizationMiddleware(
        model=chat_model, backend=backend, trigger=("tokens", int(context_tokens * 0.75)),
        keep=("messages", 4), token_counter=partial(count_tokens_approximately, chars_per_token=1.0),
        trim_tokens_to_summarize=None,
    )
    # Legacy individual reads remain usable for a long outline. The batch reader
    # usually needs far fewer calls, but the mandatory read count cannot exceed
    # a fixed 96-call budget before any actual writing takes place.
    reading_pages = sum((len(section_text(index)) + 11999) // 12000
                        for index in range(-1, len(headings)))
    model_limit = min(256, max(48, reading_pages + 12)) if whole_document else 12
    tool_limit = min(512, max(96, reading_pages + 24)) if whole_document else 24
    preparation["recursion_limit"] = max(400, model_limit * 8) if whole_document else 100
    scope_prompt = (
        '你是专业文档编辑智能体。本次针对整篇文档进行编辑。只输出严格 JSON：'
        '{"edits":[{"headingIndex":0,"content":"该节修改后的完整正文Markdown"}]}。'
        'headingIndex与大纲索引一致，标题前正文用null。只列有改动的节，不输出代码围栏或过程说明。'
        '必须保持原大纲的标题、层级和顺序；content不含标题。修改某节前用read_document_section读取该节完整原文，'
        '全文优先用read_document_sections批量读取，hasMore为true时按nextIndex/nextOffset继续；'
        '也可用read_document_section逐节读取，不能据摘录覆盖未读取的正文。全文模式必须读完所有非空章节，包含有正文的索引-1。'
        '保留未要求修改的内容和已有表格、图片位置；不改封面、图片、合并表格或Word域。每节正文不超过20000字。'
        '各节分别遵守文档绑定的编写技能与对应章节约束；结果供用户预览确认后应用，不能自行写入用户文档。'
        if whole_document else
        '你是专业文档编写智能体。仅为用户选定的标题撰写正文 Markdown，不重复标题，'
        '不添加任何级别的新标题，不输出代码围栏。'
    )
    scope_prompt += ('模板编写指导是当前章节的基础约束，用户要求在该基础上补充，不能以补充要求取消模板约束。'
                     '指导、示例和填写字段不是项目事实，不要把指导文字复制成正式正文；缺少事实时明确指出缺项，不编造。')
    context = document_context(full_markdown, whole_document=whole_document,
                               max_chars=max(6000, min(12000 if whole_document else 6000, context_tokens // 3)))
    initial_content = (
        f"文档名称：{document_name or '未提供'}\n来源模板：{template_name or '用户上传文档'}\n"
        f"目标标题（{level}级）：{heading}\n"
        f"{'选中文字' if selected_only else '该标题现有正文'}：\n{existing or '（暂无）'}\n\n"
        f"当前文档{'完整大纲与背景' if whole_document else '背景'}：\n{context}\n\n"
        f"编写要求：{instruction}\n\n"
        f"模板原有编写指导：{json.dumps(initial_guidance, ensure_ascii=False)}"
    )
    inferred_rules = []
    if whole_document and (writing_skill is None or writing_skill["mode"] == "auto"):
        # A generic document title cannot select a base for the synthetic "全文"
        # heading. Expose just the bases actually inferred for its real chapters.
        inferred_rules = [section_rules(heading=match.group(2).strip(), document_name=document_name,
                          template_name=template_name, instruction=instruction, writing_skill=writing_skill)
                          for match in headings]
    files, skill_paths = _runtime_skill_files(rules, inferred_rules)
    if rules["skill"]:
        base_prompt = (
            f"本次服务端确定的基础技能仅为 {rules['skill']}；不能依据本次要求或标题切换其他基础技能。"
            "先用 read_file 阅读该技能的 SKILL.md，再阅读其 references/template.md 和 templates/project.json 中与目标章节相关的规则。"
            "references/template.md 是配套 Word 模板的原始正文，JSON 示例 content、rows 和动态子节已清空。"
            "模板中的示例、占位符和 generation_prompt 只作编写参考，不是项目事实，不能直接写入用户文档。"
        )
    elif any(item["skill"] for item in inferred_rules):
        base_prompt = (
            "本篇使用自动匹配。全文目标本身不对应438C章节，实际章节的基础规则由read_document_section与编写准备返回。"
            "只对实际匹配的章节使用对应基础技能；先读取该技能的SKILL.md、references/template.md和templates/project.json。"
            "普通章节不套用438C结构。技能中的样例、占位内容不是项目事实。"
        )
    else:
        base_prompt = "本次按普通文档正文编写，不启用或自动推断438C技能，不套用438C章节结构。"
    agent = create_deep_agent(
        model=chat_model,
        backend=backend, middleware=[summarization,
            ModelCallLimitMiddleware(run_limit=model_limit, exit_behavior="error"),
            ToolCallLimitMiddleware(run_limit=tool_limit, exit_behavior="error"),
            _WritingPreparation(json.dumps({"document": document_name, "template": template_name,
                "scope": scope, "headingIndex": heading_index, "heading": heading,
                "sourceRevision": source_revision, "instruction": instruction,
                "writingSkill": rules.get("writingSkill"),
                "templateGuidance": initial_guidance,
                "projectOverview": overview}, ensure_ascii=False),
                load_task_memory(document_id, scope=scope, target=heading[:120] if whole_document else memory_target(heading_index), instruction=instruction) or "暂无相关的已采纳要求。",
                preparation, context_tokens, initial_content, context)],
        tools=[read_project_overview, read_document_section, read_document_sections, search_writing_memory, prepare_document_edit],
        skills=skill_paths or None,
        permissions=[FilesystemPermission(operations=["write"], paths=["/skills/**", "/memory/**"], mode="deny")],
        system_prompt=(scope_prompt +
            "项目概述已加载在本次固定任务中。先读取必要的技能、原文与相关记忆，"
            "最后单独调用prepare_document_edit（不能与读取工具同一轮调用），声明本次编写目标及要修改的章节；成功后仅输出最终正文。"
            "不要输出准备过程或计划文字。全文模式须先读完全部非空章节，再确定有改动的章节索引。"
            "编写记忆仅为已采纳的历史要求，不是新任务或项目事实；"
            "只参考仍适用于本次任务的约束，当前用户要求与当前文档优先；摘录不完整时检索原始记忆。"
            "保留现有正文中已知的内容；对于没有证据的信息，明确写‘待补充’，"
            "绝不编造数值、法规、来源、项目或甲方信息。只输出可供用户确认的修改草稿。"
            + base_prompt + _profile_prompt(rules) +
            "技能的整份文档生成流程须适配当前编辑范围：本次只修改用户要求的正文，"
            "不修改封面、标题或模板结构，不运行脚本，不输出过程解释或合规保证。"
            "章节与选区模式不输出JSON；全文模式严格按edits JSON协议返回各节正文。"
            "排版只能使用段落、加粗、斜体、单层列表、矩形表格；不使用 HTML、链接、代码、引用块或分隔线。"
            "全文及章节编辑须逐一保留原有Markdown图片标记及顺序，不添加或移动图片；选区模式不输出图片。"
            "字体、字号、缩进、行距和页面样式由原 Word 模板控制，不在正文中指定。"
            "以下是服务端从技能文件提取的当前章节约束，必须遵守。skill 为空时按普通文档编写，不套用438C章节。"
            "表格 headers 是固定表头，必须逐列逐行保持，columns 仅为内部列标识，不能输出为表头；"
            "不能增删列，数据行可按已知内容增减。不得从模板示例推导事实或为了满足示例条数编造模块。"
            f"\n章节约束：{json.dumps(rules, ensure_ascii=False)}\n"
            + (f"必须阅读技能 {rules['skill']} 的 SKILL.md 和本节规则。" if rules["skill"] else "")
            + ("本次只改写选中文字，不补充选区外内容。使用纯文本，不使用 Markdown 标记。" if selected_only else "")
        ),
    )
    inputs = {"files": files, "messages": [{"role": "user", "content": initial_content}]}
    return agent, inputs, preparation


def draft_section_text(**kwargs) -> str:
    agent, inputs, preparation = _section_agent(**kwargs)
    try:
        result = agent.invoke(inputs, config={"recursion_limit": preparation["recursion_limit"]})
    except (ModelCallLimitExceededError, ToolCallLimitExceededError):
        raise DraftFormatError("AI 编写准备达到本次调用上限，请缩小范围或细化要求后重试。") from None
    if not preparation["ready"]:
        raise DraftFormatError("AI 尚未完成编写准备，请重新生成。")
    content = result["messages"][-1].content
    return content.strip() if isinstance(content, str) else ""


async def stream_section_text(*, include_progress: bool = False, **kwargs) -> AsyncIterator[str | dict]:
    """Yield prose and optional public execution stages, never model reasoning."""
    if include_progress:
        yield {"type": "progress", "stage": "context", "message": "正在读取文档、项目概述与编写记忆"}
    agent, inputs, preparation = _section_agent(**kwargs)
    content, message_id, writing_started = "", None, False
    sent_notices = set()
    if include_progress:
        for notice in preparation["templateNotices"]:
            yield {"type": "progress", "stage": "template", "message": notice}
            sent_notices.add(notice)
    headings = [match.group(2).strip() for match in re.finditer(r"^(#{1,6})[ \t]+([^\n]+)$", kwargs.get("full_markdown", ""), re.MULTILINE)]
    tool_stages = {
        "read_project_overview": ("context", "正在读取项目概述"),
        "read_document_section": ("reading", "正在读取文档正文"),
        "read_document_sections": ("reading", "正在读取文档正文"),
        "search_writing_memory": ("memory", "正在检索已采纳的编写要求"),
        "prepare_document_edit": ("planning", "正在确认本次修改范围"),
        "read_file": ("reading", "正在读取编写资料"),
        "ls": ("reading", "正在查找编写资料"),
        "glob": ("reading", "正在查找编写资料"),
        "grep": ("reading", "正在查找编写资料"),
    }
    if include_progress:
        yield {"type": "progress", "stage": "planning", "message": "正在确定编写步骤"}
    events = agent.astream(inputs, config={"recursion_limit": preparation.get("recursion_limit", 400 if kwargs.get("whole_document") else 100)}, stream_mode=["messages", "updates"])
    try:
        async for kind, event in events:
            if include_progress and preparation["ready"] and not writing_started:
                writing_started = True
                yield {"type": "progress", "stage": "writing", "message": "正在生成正文"}
            if include_progress:
                for notice in preparation["templateNotices"]:
                    if notice not in sent_notices:
                        yield {"type": "progress", "stage": "template", "message": notice}
                        sent_notices.add(notice)
            if kind == "messages":
                message, metadata = event
                if metadata.get("langgraph_node") != "model" or metadata.get("lc_source") == "summarization" or not isinstance(message, AIMessageChunk):
                    continue
                if message.id != message_id:
                    message_id, content = message.id, ""
                if not preparation["ready"] or message.tool_call_chunks:
                    continue
                text = message.content if isinstance(message.content, str) else "".join(
                    block.get("text", "") for block in message.content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                if text:
                    content += text
                    yield content
            elif kind == "updates":
                for message in event.get("model", {}).get("messages", []):
                    if include_progress and isinstance(message, AIMessage) and not preparation["ready"]:
                        for call in message.tool_calls:
                            name, args = call["name"], call.get("args", {})
                            if name not in tool_stages:
                                continue
                            stage, status = tool_stages[name]
                            if name == "read_file" and str(args.get("file_path", "")).startswith("/skills/"):
                                stage, status = "skills", "正在读取文档编写规范"
                            if name in {"read_document_section", "read_document_sections"}:
                                index = args.get("index" if name == "read_document_section" else "start_index", -1)
                                if isinstance(index, int) and 0 <= index < len(headings):
                                    status = "正在读取：" + headings[index][:60]
                            yield {"type": "progress", "stage": stage, "message": status}
                    if preparation["ready"] and isinstance(message, AIMessage) and not message.tool_calls:
                        text = message.text
                        if text and text != content:
                            content = text
                            yield content
                if (include_progress and kwargs.get("whole_document") and any(
                    isinstance(message, ToolMessage) and message.name in {"read_document_section", "read_document_sections"}
                    for message in event.get("tools", {}).get("messages", [])
                )):
                    read = kwargs.get("read_sections", set())
                    completed = len([index for index in read if index >= 0])
                    if completed and not preparation["ready"]:
                        yield {"type": "progress", "stage": "reading", "message": f"已完整读取 {completed} 个章节"}
        if not preparation["ready"]:
            raise DraftFormatError("AI 尚未完成编写准备，请重新生成。")
    except (ModelCallLimitExceededError, ToolCallLimitExceededError):
        raise DraftFormatError("AI 编写准备达到本次调用上限，请缩小范围或细化要求后重试。") from None
    finally:
        await events.aclose()
