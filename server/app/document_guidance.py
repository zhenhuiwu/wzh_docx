"""Resolve visible advice and generation modules from the same saved content objects."""
from app.document_objects import ensure_document_objects, live_objects, revision_conflict
from app.document_selection import _resolve, SelectionError
from app.errors import AppError
from app.markdown_blocks import split, plain_text
from app.template_import import guidance_for_section
from app.document_rules import table_writing_notes

EDITABLE_KINDS = {"paragraph", "list_item", "table"}


def object_spans(connection, row) -> list[dict]:
    current = connection.execute("SELECT revision FROM documents WHERE id=%s", (row[0],)).fetchone()
    if current is None or current["revision"] != row[12]:
        raise revision_conflict(connection, row[0])
    if not ensure_document_objects(connection, row[0]):
        raise AppError(409, "OBJECT_SCOPE_UNAVAILABLE", "正文结构暂时无法定位，请刷新后再试。")
    objects = live_objects(connection, row[0])
    prefix, blocks = split(row[7])
    if len(objects) != len(blocks) or any(item["body"] != block.body for item, block in zip(objects, blocks)):
        raise AppError(409, "OBJECT_SCOPE_UNAVAILABLE", "正文对象与当前内容不一致，请刷新后再试。")
    offset, heading, result = len(prefix), -1, []
    for item, block in zip(objects, blocks):
        if block.kind == "heading":
            heading += 1
        result.append({"id": str(item["id"]), "kind": block.kind, "body": block.body,
                       "start": offset, "end": offset + len(block.body), "headingIndex": heading,
                       "label": plain_text(block.body)})
        offset += len(block.body) + len(block.gap_after)
    return result


def selection_ids(markdown: str, selection: dict, objects: list[dict]) -> list[str]:
    try:
        selected, _start, _end, _text = _resolve(markdown, selection)
    except SelectionError as error:
        raise AppError(422, "SELECTION_CHANGED", str(error)) from None
    result = [item["id"] for item in objects if any(item["start"] < block.end and item["end"] > block.start for block in selected)]
    if not result or any(item["kind"] not in EDITABLE_KINDS for item in objects if item["id"] in result):
        raise AppError(422, "SELECTION_CHANGED", "选区无法对应正文对象，请重新选择。")
    return result


def effective_advice(template: dict, markdown: str, index: int | None, objects: list[dict],
                     target_ids: list[str] | None = None) -> list[dict]:
    structure = template.get("importStructure") or {}
    blocks = structure.get("blocks", [])
    general_structure = {**structure, "blocks": [block for block in blocks if not block.get("targetBlockIds")]}
    items = guidance_for_section(general_structure, markdown, index, template.get("writingGuidance", {}))
    scoped = [item for item in objects if item["kind"] != "heading" and
              (item["id"] in target_ids if target_ids is not None else item["headingIndex"] == (index if index is not None else -1))]
    ids, live = {item["id"] for item in scoped}, {item["id"] for item in objects}
    targets = template.get("guidanceTargets", {})
    for block in blocks:
        if not block.get("targetBlockIds"):
            continue
        bound = targets.get(block["id"], [])
        if not bound or any(identifier not in live for identifier in bound):
            if ids.intersection(bound):
                raise ValueError("本范围的段落指导有目标已删除，请先核对指导范围。")
            items.append({"heading": "指定内容", "kind": "notice", "text": "有段落指导的目标已删除或未绑定；未将它用于其他内容，请核对模板及文档历史。"})
            continue
        if not ids.intersection(bound):
            continue
        labels = [item["label"] for item in scoped if item["id"] in bound]
        items.append({"id": block["id"], "heading": "指定内容", "kind": block["kind"],
                      "sourceText": block["text"], "text": template.get("writingGuidance", {}).get(block["id"], block["text"]),
                      "overridden": block["id"] in template.get("writingGuidance", {}),
                      "targetObjectIds": bound, "targetText": " / ".join(labels)})
    for item in scoped:
        source = table_writing_notes(item["body"]) if item["kind"] == "table" else ""
        text = template.get("blockGuidance", {}).get(item["id"], source)
        if text:
            items.append({"id": "object:" + item["id"], "heading": "表格填写说明" if source else "本段建议", "kind": "guidance",
                          "sourceText": source, "text": text, "overridden": item["id"] in template.get("blockGuidance", {}),
                          "targetObjectIds": [item["id"]], "targetText": item["label"]})
    return items


def writing_context(template: dict, markdown: str, objects: list[dict], indices: list[int] | None = None) -> dict:
    context = {}
    if indices is None:
        live = {item["id"] for item in objects}
        for block in (template.get("importStructure") or {}).get("blocks", []):
            bound = template.get("guidanceTargets", {}).get(block["id"], [])
            if block.get("targetBlockIds") and (not bound or not set(bound) <= live):
                raise ValueError("全文中有段落指导的目标已删除或未绑定，请核对模板及文档历史后再编写全文。")
    for index in indices if indices is not None else sorted({-1, *[item["headingIndex"] for item in objects]}):
        advice = effective_advice(template, markdown, index, objects)
        generic = [item for item in advice if not item.get("targetObjectIds")]
        bodies = [item for item in objects if item["headingIndex"] == index and item["kind"] != "heading"]
        modules = []
        if any(item.get("targetObjectIds") for item in advice):
            for item in bodies:
                if item["kind"] not in EDITABLE_KINDS:
                    continue
                applicable = [rule for rule in advice if not rule.get("targetObjectIds") or item["id"] in rule["targetObjectIds"]]
                modules.append({"start": item["start"], "end": item["end"], "text": item["body"], "guidance": applicable})
        context[index] = {"guidance": generic, "modules": modules}
    return context
