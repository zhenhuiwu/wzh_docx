"""Reviewable Word template blocks; preserve the source package during cleanup."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml import etree

from app.docx_parser import heading_level

KINDS = {"heading", "guidance", "body", "placeholder", "table", "caption", "toc", "cover", "example"}
_GUIDANCE = re.compile(r"^(?:本(?:条|节|章|项|部分|文档)(?:（[^）]*）)?(?:应|可)|为详细说明.*本条|如果要求软件在|如果本(?:条|节|章)|(?:编写|填写|撰写)(?:说明|要求|指导)[：:]|(?:请|应)在(?:此|本)(?:处|节|段)|若存在一对多则此形式表达[。；;]?$)")
_ITEM = re.compile(r"^(?:[a-zA-Z]|\d{1,2})[）).、．](?:\s*|\S)")
_CAPTION = re.compile(r"^[表图]\s*(?:\d+|[Xx])(?:[-－.]\s*(?:\d+|[Xx]))*\s*\S+")
_PLACEHOLDER = re.compile(r"^(?:文档标识号[：:]|标题[：:]\s*$|软件(?:名称|缩写)[：:]\s*$|软件版本号[。；;]?\s*$|[\[【（(](?:请填写|待填写|填写|待补充)[^\]】）)]*[\]】）)]$)")
_PROTECTED = (".//w:drawing|.//w:pict|.//w:object|.//w:fldChar|.//w:fldSimple|.//w:hyperlink"
              "|.//w:footnoteReference|.//w:endnoteReference|.//m:oMath|.//w:sdt"
              "|.//w:bookmarkStart|.//w:bookmarkEnd|.//w:sectPr|.//w:br[@w:type='page' or @w:type='column']")


def _protected(element) -> bool:
    return bool(element.xpath(_PROTECTED))


def _elements(document):
    """Use raw cell elements so merged cells are never emitted more than once."""
    for index, element in enumerate(document.element.body):
        path = f"body/{index}"
        if element.tag == qn("w:p"):
            yield path, element, None
        elif element.tag == qn("w:tbl"):
            yield path, element, None
            for row_index, row in enumerate(element.xpath("./w:tr")):
                for cell_index, cell in enumerate(row.xpath("./w:tc")):
                    for paragraph_index, paragraph in enumerate(cell.xpath("./w:p")):
                        yield f"{path}/row/{row_index}/cell/{cell_index}/p/{paragraph_index}", paragraph, path


def parse_template(path: str | Path | bytes) -> dict:
    content = path if isinstance(path, bytes) else Path(path).read_bytes()
    document = Document(BytesIO(content))
    source_hash = sha256(content).hexdigest()
    blocks, current_heading, guidance_chain, body_started = [], None, False, False
    table_kinds: dict[str, str] = {}
    for location, element, table_path in _elements(document):
        is_table = element.tag == qn("w:tbl")
        paragraph = None if is_table else Paragraph(element, document)
        text = "\n".join(element.xpath(".//w:t/text()")) if is_table else paragraph.text
        text = text.strip()
        if not text and not is_table:
            continue
        block_id = "block-" + sha256((source_hash + ":" + location).encode()).hexdigest()[:16]
        level = heading_level(paragraph) if paragraph is not None and table_path is None else None
        warning = None
        if is_table:
            kind = "table" if body_started else "cover"
            table_kinds[location] = kind
            guidance_chain = False
        elif table_path is not None:
            kind = "cover" if table_kinds[table_path] == "cover" else "body"
            if kind == "body" and _GUIDANCE.match(text):
                kind = "guidance"
            elif kind == "body" and _PLACEHOLDER.match(text):
                kind = "placeholder"
        elif (paragraph.style and re.match(r"^toc(?:\s|$)", paragraph.style.name, re.I)) or text in {"目录", "目 录"}:
            kind, level, guidance_chain = "toc", None, False
        elif level is not None:
            kind, body_started, guidance_chain = "heading", True, False
            current_heading = block_id
        elif not body_started:
            kind = "cover"
        elif _CAPTION.match(text):
            kind, guidance_chain = "caption", False
        elif _PLACEHOLDER.match(text):
            kind = "placeholder"
        elif _GUIDANCE.match(text) or (guidance_chain and _ITEM.match(text)):
            kind, guidance_chain = "guidance", True
        else:
            kind, guidance_chain = "body", False
            if len(text) <= 40 and not re.search(r"[，。；：:！？]", text):
                warning = "此段没有 Word 标题级别，请核对是否需要改为标题。"
        if kind == "placeholder" and _protected(element):
            kind = "body"
            warning = "此填写示例包含 Word 书签或域，暂保留原文；需清理时请先调整原 Word。"
        block = {"id": block_id, "path": location, "kind": kind, "text": text, "sourceText": text,
                 "headingLevel": level if kind == "heading" else None,
                 "headingId": None if kind == "heading" else current_heading,
                 "tableId": ("block-" + sha256((source_hash + ":" + table_path).encode()).hexdigest()[:16]) if table_path else None,
                 "protected": _protected(element), "isTable": is_table}
        if warning:
            block["warning"] = warning
        blocks.append(block)
    if len(blocks) > 4000:
        raise ValueError("模板内容过多，请拆分后导入。")
    return {"schemaVersion": 1, "revision": 0, "reviewed": False, "sourceHash": source_hash, "blocks": blocks}


def validate_structure(source: dict, candidate: dict) -> dict:
    """Only classification, heading level/binding and guidance text are editable."""
    if not isinstance(candidate, dict) or set(candidate) != set(source):
        raise ValueError("模板结构字段不正确。")
    if (type(candidate["schemaVersion"]) is not int or candidate["schemaVersion"] != 1
            or candidate["sourceHash"] != source["sourceHash"]
            or type(candidate["revision"]) is not int or candidate["revision"] != source["revision"]
            or type(candidate["reviewed"]) is not bool):
        raise ValueError("模板来源或结构版本已变化，请重新读取。")
    items = candidate["blocks"]
    if not isinstance(items, list) or len(items) != len(source["blocks"]):
        raise ValueError("不能新增或删除原始模板块，请重新上传修改后的 Word。")
    immutable = {"id", "path", "sourceText", "tableId", "protected", "warning", "isTable"}
    original = {block["id"]: block for block in source["blocks"]}
    result, seen = deepcopy(source), set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in original or item["id"] in seen:
            raise ValueError("模板块标识重复或不正确。")
        seen.add(item["id"])
        expected = source["blocks"][index]
        if set(item) - {"targetBlockIds"} != set(expected) - {"targetBlockIds"} or any(item.get(key) != expected.get(key) for key in immutable):
            raise ValueError("不能修改原始内容、位置或保护标记。")
        kind = item["kind"]
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("模板内容类型不正确。")
        is_table = expected["isTable"]
        if is_table and kind not in {"table", "cover"}:
            raise ValueError("整张表只能归类为表格或封面；请逐段核对单元格内容。")
        if not is_table and kind == "table":
            raise ValueError("普通段落不能改为表格。")
        level = item["headingLevel"]
        if kind == "heading":
            if item["tableId"] or type(level) is not int or not 1 <= level <= 6 or item["headingId"] is not None:
                raise ValueError("标题需要 1–6 级且不能放在表格内或绑定其他标题。")
        elif level is not None:
            raise ValueError("只有标题可以设置标题级别。")
        if item["headingId"] is not None and not isinstance(item["headingId"], str):
            raise ValueError("关联标题标识不正确。")
        if not isinstance(item["text"], str) or len(item["text"]) > max(20000, len(expected["sourceText"])):
            raise ValueError("编写指导文本超过允许长度。")
        if kind != "guidance" and item["text"] != expected["sourceText"]:
            raise ValueError("本轮只支持编辑编写指导；其他内容请修改原 Word 后重新导入。")
        if kind == "guidance" and not item["text"].strip():
            raise ValueError("编写指导不能为空。")
        if item["protected"] and kind in {"guidance", "example", "placeholder"}:
            raise ValueError("该段包含图片、域、书签或分节分页信息，不能安全清理；请保留为正文或在原 Word 中调整。")
        result["blocks"][index] = deepcopy(item)
    headings = {block["id"] for block in result["blocks"] if block["kind"] == "heading"}
    current_heading = None
    for block in result["blocks"]:
        if block["kind"] == "heading":
            current_heading = block["id"]
        elif block["kind"] != "guidance":
            block["headingId"] = current_heading
        if block["kind"] == "guidance" and block["headingId"] is not None and block["headingId"] not in headings:
            raise ValueError("关联标题不存在，请重新选择。")
        if block["kind"] == "guidance" and block["headingId"] is None:
            raise ValueError("编写指导必须关联到一个标题。")
    if result["reviewed"] and not headings:
        raise ValueError("模板至少需要一个标题才能确认。")
    validate_guidance_targets(result["blocks"])
    result["reviewed"] = candidate["reviewed"]
    return result


def clean_template(path: str | Path, structure: dict, *, target_paths: dict[str, str] | None = None) -> bytes:
    """Clear only reviewed instruction text; retain styles, cells and every other ZIP part."""
    parsed = parse_template(path)
    parsed["revision"] = structure["revision"]
    checked = validate_structure(parsed, structure)
    if not checked["reviewed"]:
        raise ValueError("请先在模板管理中核对并确认模板结构。")
    document = Document(path)
    locations = {location: element for location, element, _ in _elements(document)}
    samples, current_heading = set(), None
    for block in checked["blocks"]:
        element = locations[block["path"]]
        if block["kind"] == "heading":
            current_heading = block["id"]
        if block["kind"] in {"guidance", "example", "placeholder"}:
            if block["tableId"] is None:
                if current_heading in samples:
                    element.getparent().remove(element)
                    continue
                samples.add(current_heading)
            runs = element.xpath("./w:r")
            formatting = deepcopy(runs[0].find(qn("w:rPr"))) if runs else None
            for child in list(element):
                if child.tag != qn("w:pPr"):
                    element.remove(child)
            if formatting is not None:
                empty_run = etree.SubElement(element, qn("w:r"))
                empty_run.append(formatting)
            for numbering in element.xpath("./w:pPr/w:numPr"):
                numbering.getparent().remove(numbering)
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, document)
            old_level = heading_level(paragraph)
            level = block["headingLevel"] if block["kind"] == "heading" else None
            if old_level != level:
                properties = element.get_or_add_pPr()
                old = properties.xpath("./w:outlineLvl")
                for item in old:
                    properties.remove(item)
                outline = etree.SubElement(properties, qn("w:outlineLvl"))
                outline.set(qn("w:val"), str(level - 1 if level is not None else 9))
    if target_paths is not None or any(block.get("targetBlockIds") for block in checked["blocks"]):
        paths = cleaned_guidance_paths(document, checked, locations)
        if target_paths is not None:
            target_paths.update(paths)
    output = BytesIO()
    with ZipFile(path) as source, ZipFile(output, "w") as target:
        for entry in source.infolist():
            content = etree.tostring(document.element, xml_declaration=True, encoding="UTF-8", standalone=True) if entry.filename == "word/document.xml" else source.read(entry.filename)
            target.writestr(entry, content)
    return output.getvalue()


def validate_guidance_targets(blocks: list[dict]) -> None:
    """A local instruction may only bind surviving, non-empty body paragraphs or whole tables."""
    by_id = {block["id"]: block for block in blocks}
    for block in blocks:
        targets = block.get("targetBlockIds", [])
        if not isinstance(targets, list) or any(not isinstance(value, str) for value in targets) or len(set(targets)) != len(targets):
            raise ValueError("编写指导的目标段落或表格重复或不正确。")
        if targets and block["kind"] != "guidance":
            raise ValueError("只有编写指导可以指定目标段落或表格。")
        for target_id in targets:
            target = by_id.get(target_id)
            if target is None:
                raise ValueError("编写指导绑定的段落或表格已删除或未匹配，请重新绑定。")
            if (target["kind"] not in {"body", "table"} or target.get("tableId") or target.get("protected")
                    or not target.get("text", "").strip()):
                raise ValueError("编写指导只能绑定非空正文段落或整张表格；空填写位置、受保护内容和单元格暂不支持。")


def cleaned_guidance_paths(document, structure: dict, locations: dict) -> dict[str, str]:
    remaining = {element: location for location, element, _ in _elements(document)}
    paths = {block["id"]: remaining[locations[block["path"]]] for block in structure["blocks"]
             if block.get("path") in locations and locations[block["path"]] in remaining}
    targets = {target for block in structure["blocks"] for target in block.get("targetBlockIds", [])}
    by_id = {block["id"]: block for block in structure["blocks"]}
    for target in targets:
        element = locations.get(by_id[target].get("path"))
        if target not in paths or not "".join(element.xpath(".//w:t/text()")).strip():
            raise ValueError("编写指导目标在清理提示词后没有正文，空填写模块暂不支持，请重新绑定。")
    return paths


def guidance_for_section(structure: dict | None, markdown: str, heading_index: int | None,
                         overrides: dict[str, str] | None = None) -> list[dict]:
    """Match the complete heading path; renamed/moved ambiguous headings never borrow rules."""
    if not structure or not structure.get("reviewed") or heading_index is None or heading_index < 0:
        return []
    current = list(re.finditer(r"^(#{1,6})[ \t]+([^\n]+)$", markdown, re.MULTILINE))
    if heading_index >= len(current):
        return []
    paths, stack = {}, []
    for block in structure["blocks"]:
        if block["kind"] != "heading":
            continue
        while stack and stack[-1][0] >= block["headingLevel"]:
            stack.pop()
        stack.append((block["headingLevel"], block["sourceText"].strip(), block["id"]))
        paths[block["id"]] = tuple((level, text) for level, text, _ in stack)
    stack = []
    for match in current[:heading_index + 1]:
        level, text = len(match.group(1)), match.group(2).strip()
        # The Markdown parser escapes literal punctuation in Word headings.
        text = re.sub(r"\\([\\`*_{}\[\]<>|#])", r"\1", text)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
    ancestors = set()
    for depth in range(1, len(stack) + 1):
        matches = [key for key, path in paths.items() if path == tuple(stack[:depth])]
        if len(matches) > 1:
            raise ValueError("当前标题路径在模板中重复，无法确定 AI 提示词，请先核对模板标题。")
        if matches:
            ancestors.add(matches[0])
    if not ancestors and any(block["kind"] == "guidance" for block in structure["blocks"]):
        raise ValueError("当前标题与来源模板无法对应，无法确定 AI 提示词，请先核对标题及模板。")
    result = [{"heading": next(item["sourceText"] for item in structure["blocks"] if item["id"] == block["headingId"]),
             "id": block["id"], "kind": block["kind"], "sourceText": block["text"],
             **({"targetBlockIds": block["targetBlockIds"]} if "targetBlockIds" in block else {}),
             "text": (overrides or {}).get(block["id"], block["text"]),
             "overridden": block["id"] in (overrides or {})}
            for block in structure["blocks"]
            if block["kind"] in {"guidance", "placeholder", "example"} and block["headingId"] in ancestors]
    if not any(path == tuple(stack) for path in paths.values()) and ancestors:
        result.append({"heading": stack[-1][1], "kind": "notice",
                       "text": "当前标题未与模板精确匹配，本次仅继承已匹配上级的 AI 提示词；请核对新增或改名标题的编写要求。"})
    return result
