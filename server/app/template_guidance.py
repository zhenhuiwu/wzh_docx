"""Prove template-to-document guidance bindings while creating a document."""

from io import BytesIO
from uuid import uuid4

from docx import Document

from app.docx_parser import parse_docx
from app.markdown_blocks import split
from app.template_import import _elements, validate_guidance_targets


def bind_guidance_targets(cleaned: bytes, structure: dict, markdown: str, objects,
                          *, target_paths: dict[str, str]) -> dict[str, list[str]]:
    """Return guidance ID -> persisted object IDs, rejecting every unproven target.

    target_paths comes from clean_template/clean_version in this same creation.
    Markers exist only in a temporary in-memory copy; saved Word bytes are untouched.
    """
    validate_guidance_targets(structure["blocks"])
    instructions = [block for block in structure["blocks"] if block.get("targetBlockIds")]
    if not instructions:
        return {}
    if parse_docx(BytesIO(cleaned), outline_headings=True).markdown != markdown:
        raise ValueError("模板正文已变化，无法确认编写指导目标，请重新创建文档。")
    _, blocks = split(markdown)
    rows = list(objects)
    if len(rows) != len(blocks) or any(row["body"] != block.body or row["kind"] != block.kind
                                       for row, block in zip(rows, blocks)):
        raise ValueError("文档对象与模板正文未对齐，无法绑定编写指导。")
    document = Document(BytesIO(cleaned))
    locations = {path: element for path, element, _ in _elements(document)}
    markers = {}
    for target in dict.fromkeys(value for block in instructions for value in block["targetBlockIds"]):
        element = locations.get(target_paths.get(target))
        texts = [] if element is None else element.xpath(".//w:t")
        if not texts or not any(text.text and text.text.strip() for text in texts):
            raise ValueError("编写指导目标没有可绑定的正文或表格，空填写模块暂不支持，请调整模板。")
        marker = "GUIDANCETARGET" + uuid4().hex
        markers[target] = marker
        texts[0].text = marker + (texts[0].text or "")
    marked = BytesIO()
    document.save(marked)
    _, marked_blocks = split(parse_docx(BytesIO(marked.getvalue()), outline_headings=True).markdown)
    if len(marked_blocks) != len(blocks):
        raise ValueError("编写指导目标跨越多个正文对象，无法安全绑定，请调整模板。")
    bindings = {}
    for index, (original, changed) in enumerate(zip(blocks, marked_blocks)):
        restored = changed.body
        for target, marker in markers.items():
            if marker in restored:
                if target in bindings or original.kind not in {"paragraph", "list_item", "table"}:
                    raise ValueError("编写指导目标无法唯一对应正文段落或整表，请调整模板。")
                bindings[target] = str(rows[index]["id"])
                restored = restored.replace(marker, "")
        if restored != original.body or changed.kind != original.kind:
            raise ValueError("编写指导目标与原文结构不一致，请调整模板后重试。")
    if set(bindings) != set(markers):
        raise ValueError("部分编写指导目标未生成正文对象，请重新绑定模板。")
    return {block["id"]: list(dict.fromkeys(bindings[target] for target in block["targetBlockIds"]))
            for block in instructions}
