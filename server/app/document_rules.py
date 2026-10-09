"""Chapter rules from the bundled skills and the formats Word can preserve."""

from __future__ import annotations

import json
from pathlib import Path
import re
import markdown

from app.docx_export import _html_blocks, _text


SKILL_ROOT = Path(__file__).resolve().parents[1] / "workspaces" / "GJB438C-DocSkill" / "skills"


class DraftFormatError(ValueError):
    """A safe, user-facing validation error; never contains provider output."""


def _title(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"^\s*\d+(?:\.\d+)*[.、\s]*", "", text)).casefold()


def _profile_rules(writing_skill: dict | None) -> dict:
    """Read a document-bound snapshot, never infer or reload an explicit choice."""
    result = {"writingSkill": None, "instructions": "", "documentInstructions": "",
              "constraints": {"forbiddenTerms": [], "maxChars": None}}
    if writing_skill is None:
        return result
    if not isinstance(writing_skill, dict) or writing_skill.get("mode") not in {"auto", "none", "selected"}:
        raise DraftFormatError("文档的编写 Skill 配置不正确，请重新选择。")
    mode = writing_skill["mode"]
    kind = writing_skill.get("baseKind", "general") if mode == "selected" else "general"
    if kind not in {"general", "srs", "sdd"}:
        raise DraftFormatError("文档的编写 Skill 基础类型不正确，请重新选择。")
    result["writingSkill"] = {key: writing_skill.get(key) for key in ("id", "name", "revision")}
    result["writingSkill"].update(mode=mode, baseKind=kind)
    extra = writing_skill.get("documentInstructions", "")
    if not isinstance(extra, str):
        raise DraftFormatError("文档的额外编写要求不正确。")
    result["documentInstructions"] = extra
    if mode != "selected":
        return result
    instructions = writing_skill.get("instructions", "")
    terms = writing_skill.get("forbiddenTerms", [])
    maximum = writing_skill.get("maxChars")
    if (not isinstance(instructions, str) or not isinstance(terms, list)
            or any(not isinstance(term, str) or not term.strip() for term in terms)
            or (maximum is not None and (type(maximum) is not int or not 100 <= maximum <= 20000))):
        raise DraftFormatError("文档的编写 Skill 约束不正确，请重新选择。")
    result["instructions"] = instructions
    result["constraints"] = {"forbiddenTerms": list(terms), "maxChars": maximum}
    return result


def section_rules(*, heading: str, document_name: str = "", template_name: str = "",
                  instruction: str = "", existing: str = "", selected_only: bool = False,
                  writing_skill: dict | None = None) -> dict:
    result = {"skill": "", "chapter": {}, "tables": [], **_profile_rules(writing_skill)}
    # Old documents and explicit auto mode retain historical inference.
    kind = ""
    if writing_skill is not None and writing_skill.get("mode") != "auto":
        selected = result["writingSkill"]
        kind = selected["baseKind"] if selected["mode"] == "selected" and selected["baseKind"] != "general" else ""
    else:
        for label in (template_name, document_name, instruction, heading):
            matches = [name for name, pattern in (
                ("srs", r"\bSRS\b|软件需求规格说明|软件能力需求|软件外部接口需求"),
                ("sdd", r"\bSDD\b|软件设计说明|CSCI.*设计|接口设计"),
            ) if re.search(pattern, label, re.IGNORECASE)]
            if len(matches) == 1:
                kind = matches[0]
                break
    if not kind:
        return result
    if result["writingSkill"] is not None and result["writingSkill"]["mode"] == "auto":
        result["writingSkill"]["baseKind"] = kind
    result["skill"] = f"word-fillter-438c-{kind}"
    path = SKILL_ROOT / result["skill"] / "templates" / "project.json"
    try:
        structure = json.loads(path.read_text(encoding="utf-8"))["structure"]
    except (OSError, ValueError, KeyError):
        raise DraftFormatError("所选 438C 编写 Skill 文件缺失或无效，请检查技能库。") from None
    chapters = {number: section for number, section in structure.items()}
    for section in structure.values():
        chapters.update({item["id"]: item for item in section.get("placeholders", [])})
    number = next((key for key, value in chapters.items()
                   if _title(value.get("title", value.get("name", ""))) == _title(heading)), "")
    if not number:
        match = re.match(r"^\s*(\d+(?:\.\d+)*)", heading)
        number = match[1] if match else ""
        while number and number not in chapters:
            number = number.rpartition(".")[0]
    if number:
        chapter = chapters[number]
        result["chapter"] = {"id": number, **{key: chapter[key] for key in
            ("title", "name", "description", "replacement_pattern") if key in chapter}}
        if not selected_only:
            existing_tables = [block for block in _html_blocks(existing) if block.tag == "table"]
            for index, table in enumerate(chapter.get("tables", [])):
                headers = table["locator_rows"]
                if index < len(existing_tables):
                    rows = existing_tables[index].xpath(".//tr")
                    actual = [[_text(cell) for cell in row.xpath("./th|./td")]
                              for row in rows[:table["data_start_row"]]]
                    if len(actual) == len(headers) and all(len(row) == len(table["columns"]) for row in actual):
                        headers = actual
                result["tables"].append({"headers": headers, "columns": table["columns"]})
    return result


def validate_constraints(content: str, rules: dict) -> str:
    """Check visible prose only, leaving existing Word/Markdown structure intact.

    Also use this on the resulting complete section after appending a draft.
    The caller retains the original content; this check does not normalize it.
    """
    # Markdown emphasis cannot split a forbidden word and bypass the check.
    visible = "\n".join(_text(block) for block in _html_blocks(content))
    constraints = rules.get("constraints", {})
    if any(term.casefold() in visible.casefold() for term in constraints.get("forbiddenTerms", [])):
        raise DraftFormatError("正文包含编写 Skill 禁用的词语，请重新生成。")
    maximum = constraints.get("maxChars")
    if maximum is not None and len(visible) > maximum:
        raise DraftFormatError(f"正文超过编写 Skill 的 {maximum} 字上限，请缩短后重新生成。")
    return content


def validate_draft(content: str, rules: dict, *, selected_only: bool = False,
                   complete: bool = True, heading: str = "", preserve_images: bool = False,
                   existing: str = "") -> str:
    content = content.strip()
    if len(content) > 20000 or (complete and not content):
        raise DraftFormatError("AI 正文为空或超过 20000 字，请缩小编写范围后重试。")
    if re.search(r"</?[a-zA-Z][^>]*>|<!--|<!DOCTYPE|^\s*(?:```|~~~)", content, re.MULTILINE):
        raise DraftFormatError("正文不能包含 HTML 或代码围栏，请重新生成。")
    if content.startswith(("{", "[")):
        try:
            value = json.loads(content)
        except ValueError:
            pass
        else:
            if isinstance(value, (dict, list)):
                raise DraftFormatError("AI 返回了数据结构，应只输出正文。")
    blocks = _html_blocks(content)
    if any(node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"} for block in blocks for node in block.iter()):
        raise DraftFormatError("草稿只能包含正文；请通过大纲添加标题。")
    allowed = {"p", "strong", "em", "br", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td"}
    if preserve_images:
        allowed.add("img")
        images = lambda source: [tuple(sorted(node.attrib.items())) for block in _html_blocks(source) for node in block.iter("img")]
        current_images, original_images = images(content), images(existing)
        if current_images != (original_images if complete else original_images[:len(current_images)]):
            raise DraftFormatError("正文必须保留原有图片及顺序，请重新生成。")
    if any(node.tag not in allowed or any(not (key == "style" and node.tag in {"th", "td"}) and not (preserve_images and node.tag == "img" and key in {"src", "alt"})
                                         for key in node.attrib)
           for block in blocks for node in block.iter()):
        raise DraftFormatError("正文仅支持段落、加粗、斜体、列表和表格，请重新生成。")
    if any(block.xpath(".//li/ul|.//li/ol|.//li/p|.//table") for block in blocks):
        raise DraftFormatError("正文不支持嵌套列表或嵌套表格，请使用单层结构。")
    if selected_only and any(node.tag not in {"p", "br"} for block in blocks for node in block.iter()):
        raise DraftFormatError("选中文字改写应为纯文本，不能添加 Markdown 格式。")
    if complete and heading and blocks and _title(_text(blocks[0]).rstrip("：:")) == _title(heading):
        raise DraftFormatError("正文重复了当前标题，请重新生成。")
    if complete:
        validate_constraints(content, rules)
    if complete and not selected_only:
        # Use the installed Markdown parser's splitter: it understands escaped pipes.
        processor = markdown.Markdown(extensions=["tables"]).parser.blockprocessors["table"]
        for chunk in re.split(r"\n\s*\n", content):
            lines = chunk.splitlines()
            if len(lines) > 1 and "|" in lines[0] and re.fullmatch(r"[\s|:\-]+", lines[1]):
                processor.border = lines[0].strip().startswith("|") or lines[0].strip().endswith("|")
                width = len(processor._split_row(lines[0].strip()))
                if any(len(processor._split_row(line.strip())) != width for line in lines[1:]):
                    raise DraftFormatError("表格各行列数必须一致，请重新生成。")
        tables = [block for block in blocks if block.tag == "table"]
        expected = rules.get("tables", [])
        if expected and len(tables) != len(expected):
            raise DraftFormatError("本节表格数量与技能模板不一致，请重新生成。")
        for index, table in enumerate(tables):
            rows = [[_text(cell) for cell in row.xpath("./th|./td")] for row in table.xpath(".//tr")]
            if not rows or any(len(row) != len(rows[0]) for row in rows):
                raise DraftFormatError("表格各行列数必须一致，请重新生成。")
            if expected:
                headers = expected[index]["headers"]
                if len(rows[0]) != len(expected[index]["columns"]) or rows[:len(headers)] != headers:
                    raise DraftFormatError("表格的表头或列顺序不符合技能模板，请重新生成。")
                if len(rows) <= len(headers):
                    raise DraftFormatError("表格不能只有表头，未知数据请明确填写待补充。")
    return content
