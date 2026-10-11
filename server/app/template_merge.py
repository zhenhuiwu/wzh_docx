"""A06 模板结构的纯函数部分（不碰数据库、不碰 Word）：草稿校验、规范化哈希、按块 ID 对比、三方合并（变基/恢复）。

结构约定（17 §3.5、§3.7）：
- 结构与 importStructure 同形（schemaVersion/revision/reviewed/sourceHash/blocks），草稿另带 baseFileId（块 path 指向的 Word）；
- 有 path 的块对应基底 Word 中的段落或表格；path 为 None 的块是新增内容（发布时插入）或“虚拟指导”（不进 Word）；
- 表格块可带 cells（二维文字），只在草稿里有改动时保存；单元格段落（tableId 非空）由服务端维护。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from difflib import SequenceMatcher

from app.template_import import KINDS
from app.template_word import (MAX_BLOCKS, NEW_ID, NEW_KINDS, PROP_LABELS, STYLE_PROPS, TemplateEditError, _rebind_headings,
                               validate_value)

MAX_TEXT = 20000
CELL_KINDS = {"body", "guidance", "example", "placeholder", "cover"}
_PROTECTED_KIND = "该段包含图片、域、书签或分节分页信息，不能安全清理；请保留为正文或在原 Word 中调整。"
_STALE = "模板块与 Word 位置对不上，请刷新后重试。"


class TooManyBlocks(ValueError):
    pass


# ---------------------------------------------------------------- 哈希与语义比较

def canonical_hash(structure: dict) -> str:
    """17 §3.4-4：去掉易变的顶层 revision（以及草稿专用的 baseFileId）后的规范化 JSON 哈希。"""
    data = {key: value for key, value in structure.items() if key not in {"revision", "baseFileId"}}
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def with_cells(structure: dict, analysis: dict) -> dict:
    """给表格块补上 Word 里的单元格文字（草稿已改的保留）。"""
    result = deepcopy(structure)
    for block in result["blocks"]:
        if block.get("isTable") and block.get("path") and "cells" not in block:
            table = analysis["tables"].get(block["path"])
            if table is not None:
                block["cells"] = deepcopy(table["cells"])
    return result


def fields(block: dict) -> dict:
    """参与对比与合并的字段（17 §3.5：text/kind/headingLevel/指导绑定/单元格）。"""
    result = {"kind": block["kind"], "text": block["text"], "headingLevel": block.get("headingLevel")}
    if block["kind"] == "guidance":
        result["headingId"] = block.get("headingId")
    for r, row in enumerate(block.get("cells") or []):
        for c, value in enumerate(row):
            result[f"cell:{r}:{c}"] = value
    return result


def semantic(structure: dict) -> list:
    return [(block["id"], block.get("path"), bool(block.get("unplaced")), fields(block)) for block in structure["blocks"]]


# ---------------------------------------------------------------- 草稿校验（PUT draft）

def _error(block_id, message: str, **extra) -> dict:
    return {"blockId": block_id, "message": message, **extra}


def validate_format(candidate, base_styles: dict) -> dict:
    styles = candidate.get("styles") if isinstance(candidate, dict) else None
    if styles is None:
        return {"styles": deepcopy(base_styles)}
    if not isinstance(styles, dict):
        raise TemplateEditError("TEMPLATE_STRUCTURE_INVALID", [_error(None, "格式设置不正确。")])
    result = deepcopy(base_styles)
    problems = []
    for key, props in styles.items():
        if key not in base_styles or not isinstance(props, dict):
            problems.append(_error(None, f"模板中没有可修改的“{key}”样式。"))
            continue
        for prop, value in props.items():
            if prop not in STYLE_PROPS:
                problems.append(_error(None, f"“{key}”不能修改“{prop}”。"))
                continue
            if value == base_styles[key].get(prop):
                result[key][prop] = value
                continue
            if not validate_value(prop, value):
                problems.append(_error(None, f"“{key}”的{PROP_LABELS.get(prop, prop)}取值不正确。", style=key, prop=prop))
                continue
            result[key][prop] = value
    if problems:
        raise TemplateEditError("TEMPLATE_STRUCTURE_INVALID", problems)
    return {"styles": result}


def validate_draft(candidate, reference: dict, analysis: dict) -> list[dict]:
    """校验客户端提交的块列表，返回规范化后的块（服务端字段一律取 reference/analysis，不信任客户端）。

    reference：path 与基底 Word 一致的结构（当前草稿或其 parent 版本），用来核对块 ID ↔ path。
    """
    blocks = candidate.get("blocks") if isinstance(candidate, dict) else None
    if not isinstance(blocks, list):
        raise TemplateEditError("TEMPLATE_STRUCTURE_INVALID", [_error(None, "模板结构不正确，请刷新后重试。")])
    if len(blocks) > MAX_BLOCKS:
        raise TooManyBlocks()
    by_path = {block["path"]: block for block in reference["blocks"] if block.get("path")}
    with_path = {block["id"] for block in reference["blocks"] if block.get("path")}
    errors: list[dict] = []
    top: list[dict] = []
    children: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for item in blocks:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not NEW_ID.match(item["id"]) or item["id"] in seen:
            errors.append(_error(item.get("id") if isinstance(item, dict) else None, "模板块标识重复或不正确。"))
            continue
        block_id = item["id"]
        seen.add(block_id)
        kind, text, level = item.get("kind"), item.get("text"), item.get("headingLevel")
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            errors.append(_error(block_id, "文字超过允许长度。"))
            continue
        if not isinstance(kind, str) or kind not in KINDS:
            errors.append(_error(block_id, "模板内容类型不正确。"))
            continue
        if kind == "heading" and (type(level) is not int or not 1 <= level <= 6):
            errors.append(_error(block_id, "标题级别需要在 1–6 之间。"))
            continue
        path = item.get("path")
        if path:
            ref = by_path.get(path)
            if ref is None or ref["id"] != block_id:
                errors.append(_error(block_id, _STALE))
                continue
            block = {key: deepcopy(ref[key]) for key in ("id", "path", "sourceText", "protected", "isTable", "tableId") }
            if ref.get("warning"):
                block["warning"] = ref["warning"]
            info = analysis["blocks"].get(path) or {}
            ref_kind = ref["kind"]
            if block["isTable"]:
                if kind not in {"table", "cover"}:
                    errors.append(_error(block_id, "整张表只能归类为表格或封面。"))
                    continue
            elif kind == "table":
                errors.append(_error(block_id, "普通段落不能改为表格。"))
                continue
            if block["tableId"] and kind not in CELL_KINDS | {ref_kind}:
                errors.append(_error(block_id, "单元格内容不能设为标题或目录。"))
                continue
            if (ref_kind == "toc") != (kind == "toc"):
                errors.append(_error(block_id, "目录（保留原样，导出后在 Word 中更新域）不能修改类型。"))
                continue
            if block["protected"] and kind in {"guidance", "example", "placeholder"}:
                errors.append(_error(block_id, _PROTECTED_KIND))
                continue
            block["kind"] = kind
            block["headingLevel"] = level if kind == "heading" else None
            block["headingId"] = item.get("headingId") if kind == "guidance" and isinstance(item.get("headingId"), str) else None
            if block["isTable"]:
                block["text"] = ref["text"]
                cells = item.get("cells")
                if cells is not None:
                    problem = _check_cells(block_id, cells, analysis["tables"].get(path))
                    if problem:
                        errors.extend(problem)
                        continue
                    if cells != analysis["tables"][path]["cells"]:
                        block["cells"] = deepcopy(cells)
            elif kind == "guidance":
                if not text.strip():
                    errors.append(_error(block_id, "编写指导不能为空。"))
                    continue
                block["text"] = text
            else:
                new_text = text.strip()
                if block["tableId"]:
                    if new_text != ref["sourceText"].strip():
                        errors.append(_error(block_id, "单元格文字请在表格中修改。"))
                        continue
                elif new_text != ref["sourceText"].strip():
                    if info.get("textLock"):
                        errors.append(_error(block_id, info["textLock"]))
                        continue
                    if not new_text:
                        errors.append(_error(block_id, "段落文字不能为空；不需要的段落请删除。"))
                        continue
                block["text"] = new_text
            if block["tableId"]:
                children.setdefault(block["tableId"], []).append(block)
            else:
                top.append(block)
            continue
        # 新增块或虚拟指导
        if block_id in with_path:
            errors.append(_error(block_id, _STALE))
            continue
        if item.get("isTable") or item.get("tableId"):
            errors.append(_error(block_id, "不能在线新增表格或单元格；请在 Word 中修改后上传为新草稿。"))
            continue
        if kind not in NEW_KINDS:
            errors.append(_error(block_id, "新增内容只能是标题、正文、编写指导、示例或填写位置。"))
            continue
        if not text.strip():
            errors.append(_error(block_id, "编写指导不能为空。" if kind == "guidance" else "新增内容不能为空。"))
            continue
        guidance = kind == "guidance"
        block = {"id": block_id, "path": None, "kind": kind, "text": text if guidance else text.strip(),
                 "sourceText": text if guidance else "", "headingLevel": level if kind == "heading" else None,
                 "headingId": item.get("headingId") if guidance and isinstance(item.get("headingId"), str) else None,
                 "tableId": None, "protected": False, "isTable": False}
        if guidance:
            block["virtual"] = True
            if item.get("unplaced"):
                block["unplaced"] = True
        top.append(block)
    # 单元格段落随表格：必须齐全，顺序以模板为准
    present = {block["path"] for block in top if block.get("path")}
    reference_children: dict[str, list[dict]] = {}
    for ref in reference["blocks"]:
        if ref.get("tableId") and ref.get("path"):
            reference_children.setdefault(ref["tableId"], []).append(ref)
    result: list[dict] = []
    for block in top:
        result.append(block)
        if block["isTable"]:
            given = {child["id"]: child for child in children.get(block["id"], [])}
            for ref in reference_children.get(block["id"], []):
                if ref["id"] not in given:
                    errors.append(_error(ref["id"], "单元格段落随表格保留，不能单独删除。"))
                    continue
                result.append(given[ref["id"]])
    for path, ref in by_path.items():
        if ref.get("tableId") or path in present:
            continue
        info = analysis["blocks"].get(path) or {}
        if ref["isTable"]:
            errors.append(_error(ref["id"], "表格不能在线删除；请在 Word 中修改后上传为新草稿。"))
        elif info.get("deleteLock"):
            errors.append(_error(ref["id"], info["deleteLock"]))
    if not any(block["kind"] == "heading" for block in result):
        errors.append(_error(None, "模板至少需要一个标题。"))
    if errors:
        unsupported = any(problem["message"] not in {_STALE, "模板块标识重复或不正确。"} for problem in errors)
        raise TemplateEditError("TEMPLATE_EDIT_UNSUPPORTED" if unsupported else "TEMPLATE_STRUCTURE_INVALID", errors[:50])
    _rebind_headings(result)
    return result


def _check_cells(block_id: str, cells, table: dict | None) -> list[dict]:
    if table is None:
        return [_error(block_id, _STALE)]
    base = table["cells"]
    if not isinstance(cells, list) or len(cells) != len(base) or any(
            not isinstance(row, list) or len(row) != len(old) or any(not isinstance(value, str) or len(value) > MAX_TEXT for value in row)
            for row, old in zip(cells, base)):
        return [_error(block_id, "表格行列与模板不一致；增删行列请在 Word 中修改后上传为新草稿。")]
    locked = {tuple(cell) for cell in table["lockedCells"]}
    problems = []
    for r, (row, old) in enumerate(zip(cells, base)):
        for c, (value, before) in enumerate(zip(row, old)):
            if value == before:
                continue
            if table["tableEditable"] == "none" or (r, c) in locked:
                reason = next((item["message"] for item in table["tableEditReasons"] if [r, c] in (item.get("cells") or [])), None) \
                    or (table["tableEditReasons"][0]["message"] if table["tableEditReasons"] else "此单元格不能在线修改。")
                problems.append(_error(block_id, reason, cell=[r, c]))
    return problems


# ---------------------------------------------------------------- 对比（按块 ID）

def category(block: dict) -> str:
    if block["kind"] == "heading":
        return "headings"
    if block["kind"] == "guidance":
        return "guidance"
    if block.get("isTable") or block.get("tableId"):
        return "tables"
    return "body"


def _view(block: dict) -> dict:
    view = {"kind": block["kind"], "text": block["text"], "headingLevel": block.get("headingLevel")}
    if block.get("cells") is not None:
        view["cells"] = block["cells"]
    return view


def _headings(blocks: list[dict]) -> dict[str, str]:
    names = {block["id"]: block["text"] for block in blocks if block["kind"] == "heading"}
    result, current = {}, None
    for block in blocks:
        if block["kind"] == "heading":
            current = block["id"]
        result[block["id"]] = names.get(block.get("headingId") or current, "") if block["kind"] != "heading" else block["text"]
    return result


def _listed(block: dict) -> bool:
    # 单元格里的非指导段落随表格的 cells 一起比较，不单列
    return not block.get("tableId") or block["kind"] == "guidance"


def style_diff(a: dict, b: dict) -> list[dict]:
    items = []
    for key in b:
        for prop in STYLE_PROPS:
            before, after = (a.get(key) or {}).get(prop), (b.get(key) or {}).get(prop)
            if before != after:
                items.append({"style": key, "prop": prop, "label": PROP_LABELS.get(prop, prop), "before": before, "after": after})
    return items


def diff(a: dict, b: dict, a_styles: dict, b_styles: dict) -> dict:
    """a、b 需已用 with_cells 补齐表格文字。"""
    old = {block["id"]: block for block in a["blocks"] if _listed(block)}
    new = {block["id"]: block for block in b["blocks"] if _listed(block)}
    old_names, new_names = _headings(a["blocks"]), _headings(b["blocks"])
    items: list[dict] = []
    common_old = [block["id"] for block in a["blocks"] if not block.get("tableId") and block["id"] in new]
    common_new = [block["id"] for block in b["blocks"] if not block.get("tableId") and block["id"] in old]
    stay: set[str] = set()
    for tag, i1, i2, _j1, _j2 in SequenceMatcher(None, common_old, common_new, autojunk=False).get_opcodes():
        if tag == "equal":
            stay.update(common_old[i1:i2])
    moved = {block_id for block_id in common_new if block_id not in stay}
    for block in b["blocks"]:
        if not _listed(block):
            continue
        before = old.get(block["id"])
        if before is None:
            items.append({"blockId": block["id"], "change": "added", "category": category(block), "heading": new_names.get(block["id"], ""),
                          "before": None, "after": _view(block), "fields": []})
            continue
        changed = [key for key in sorted(set(fields(before)) | set(fields(block))) if fields(before).get(key) != fields(block).get(key)]
        if block["id"] in moved:
            changed.append("order")
        if changed:
            change = "moved" if changed == ["order"] else "modified"
            items.append({"blockId": block["id"], "change": change, "category": category(block), "heading": new_names.get(block["id"], ""),
                          "before": _view(before), "after": _view(block), "fields": changed})
    for block in a["blocks"]:
        if _listed(block) and block["id"] not in new:
            items.append({"blockId": block["id"], "change": "removed", "category": category(block), "heading": old_names.get(block["id"], ""),
                          "before": _view(block), "after": None, "fields": []})
    styles = style_diff(a_styles, b_styles)
    summary = {"headings": 0, "body": 0, "tables": 0, "guidance": 0, "format": len(styles)}
    for item in items:
        summary[item["category"]] += 1
    return {"summary": summary, "blocks": items, "styles": styles}


# ---------------------------------------------------------------- 三方合并（变基、恢复）

def _order(blocks: list[dict]) -> list[str]:
    return [block["id"] for block in blocks if not block.get("tableId")]


def merge3(base: dict, theirs: dict, mine: dict, styles: tuple[dict, dict, dict], resolutions: dict | None = None,
           undeletable: set[str] | None = None) -> dict:
    """按块 ID 三方合并（17 §3.5）。结构需已 with_cells。

    返回 {blocks, styles, conflicts, notes}；blocks 以 theirs 的 Word 为基底（path 取 theirs），
    mine 独有的块（新增或恢复回来的）path 为 None，发布时插入。conflicts 未在 resolutions 中给出选择时，
    结果里该字段暂取 theirs。
    """
    resolutions = resolutions or {}
    undeletable = undeletable or set()
    B = {block["id"]: block for block in base["blocks"]}
    T = {block["id"]: block for block in theirs["blocks"]}
    M = {block["id"]: block for block in mine["blocks"]}
    names = _headings(theirs["blocks"]) | {k: v for k, v in _headings(mine["blocks"]).items()}
    conflicts: list[dict] = []
    notes: list[dict] = []
    merged: dict[str, dict] = {}   # id -> {"source": "theirs"|"mine", "fields": {...}}

    def conflict(block_id, field, b, t, m):
        take = resolutions.get((block_id, field))
        source = M.get(block_id) or T.get(block_id) or B.get(block_id)
        conflicts.append({"blockId": block_id, "field": field, "base": b, "theirs": t, "mine": m,
                          "heading": names.get(block_id, ""), "text": (source["text"] if source else "")[:40],
                          "category": category(source) if source else "body", "resolved": take})
        return take

    ids = list(dict.fromkeys([*T, *M, *B]))
    for block_id in ids:
        b, t, m = B.get(block_id), T.get(block_id), M.get(block_id)
        if t is not None and t.get("tableId") or m is not None and m.get("tableId") and t is None:
            continue  # 单元格段落随表格处理
        if b is None:
            if t is not None:
                merged[block_id] = {"source": "theirs", "fields": fields(t)}
            elif m is not None:
                merged[block_id] = {"source": "mine", "fields": fields(m)}
            continue
        if t is None and m is None:
            continue
        if t is None:
            if fields(m) == fields(b):
                continue
            if conflict(block_id, "exists", True, False, True) == "mine":
                merged[block_id] = {"source": "mine", "fields": fields(m)}
            continue
        if m is None:
            if fields(t) == fields(b) and block_id not in undeletable:
                continue
            if block_id in undeletable and fields(t) == fields(b):
                notes.append({"blockId": block_id, "message": "该块不能在线删除，已保留。"})
                merged[block_id] = {"source": "theirs", "fields": fields(t)}
                continue
            take = conflict(block_id, "exists", True, True, False)
            if take != "mine" or block_id in undeletable:
                merged[block_id] = {"source": "theirs", "fields": fields(t)}
            continue
        fb, ft, fm = fields(b), fields(t), fields(m)
        result = {}
        for key in dict.fromkeys([*ft, *fm]):
            bv, tv, mv = fb.get(key), ft.get(key), fm.get(key)
            if key not in ft:
                continue  # theirs 表格行列已变：以 theirs 为准
            if mv == bv or key not in fm:
                result[key] = tv
            elif tv == bv or tv == mv:
                result[key] = mv
            else:
                result[key] = mv if conflict(block_id, key, bv, tv, mv) == "mine" else tv
        merged[block_id] = {"source": "theirs", "fields": result}
    # 顺序：一侧没动顺序就取另一侧；都动了以 theirs 为准
    base_order = _order(base["blocks"])
    theirs_order, mine_order = _order(theirs["blocks"]), _order(mine["blocks"])

    def common(order, other):
        others = set(other)
        return [block_id for block_id in order if block_id in others]
    theirs_moved = common(theirs_order, base_order) != common(base_order, theirs_order)
    mine_moved = common(mine_order, base_order) != common(base_order, mine_order)
    primary, secondary = (mine_order, theirs_order) if (mine_moved and not theirs_moved) else (theirs_order, mine_order)
    sequence = [block_id for block_id in primary if block_id in merged]
    placed = set(sequence)
    for index, block_id in enumerate(secondary):
        if block_id in merged and block_id not in placed:
            anchor = next((secondary[j] for j in range(index - 1, -1, -1) if secondary[j] in placed), None)
            position = sequence.index(anchor) + 1 if anchor is not None else 0
            sequence.insert(position, block_id)
            placed.add(block_id)
    blocks: list[dict] = []
    for block_id in sequence:
        entry = merged[block_id]
        values = entry["fields"]
        if entry["source"] == "theirs":
            block = deepcopy(T[block_id])
            block.pop("cells", None)
        else:
            source = M[block_id]
            if source.get("isTable"):
                notes.append({"blockId": block_id, "message": "表格不能在线恢复或新增，已略过；请在 Word 中修改后上传为新草稿。"})
                continue
            kind = values["kind"] if values["kind"] in NEW_KINDS else "body"
            block = {"id": block_id, "path": None, "kind": kind, "text": values["text"], "sourceText": values["text"] if kind == "guidance" else "",
                     "headingLevel": values.get("headingLevel") if kind == "heading" else None, "headingId": values.get("headingId"),
                     "tableId": None, "protected": False, "isTable": False}
            if kind == "guidance":
                block["virtual"] = True
                if source.get("unplaced"):
                    block["unplaced"] = True
            blocks.append(block)
            continue
        block["kind"] = values["kind"]
        block["text"] = values["text"] if not block.get("isTable") else block["text"]
        block["headingLevel"] = values.get("headingLevel") if values["kind"] == "heading" else None
        if values["kind"] == "guidance":
            block["headingId"] = values.get("headingId")
        if block.get("isTable"):
            grid = deepcopy(T[block_id].get("cells") or [])
            for r, row in enumerate(grid):
                for c in range(len(row)):
                    row[c] = values.get(f"cell:{r}:{c}", row[c])
            if grid != (T[block_id].get("cells") or []):
                block["cells"] = grid
        blocks.append(block)
        if block.get("isTable"):
            for child in theirs["blocks"]:
                if child.get("tableId") != block_id:
                    continue
                item = deepcopy(child)
                b, m = B.get(child["id"]), M.get(child["id"])
                if m is not None and b is not None and m["kind"] != b["kind"] and child["kind"] == b["kind"]:
                    item["kind"] = m["kind"]
                if item["kind"] == "guidance" and m is not None and m["kind"] == "guidance":
                    if b is None or b["text"] != m["text"]:
                        item["text"] = m["text"]
                blocks.append(item)
    # 样式（每个“样式 · 属性”）
    base_styles, theirs_styles, mine_styles = styles
    result_styles = deepcopy(theirs_styles)
    for key, props in theirs_styles.items():
        for prop in STYLE_PROPS:
            bv = (base_styles.get(key) or {}).get(prop)
            tv = props.get(prop)
            mv = (mine_styles.get(key) or {}).get(prop, bv)
            if mv == bv or tv == mv:
                continue
            if tv == bv:
                result_styles[key][prop] = mv
                continue
            field = f"style:{key}:{prop}"
            take = resolutions.get((None, field))
            conflicts.append({"blockId": None, "field": field, "base": bv, "theirs": tv, "mine": mv, "heading": "",
                              "text": f"{key} · {PROP_LABELS.get(prop, prop)}", "category": "format", "resolved": take})
            if take == "mine":
                result_styles[key][prop] = mv
    _rebind_headings(blocks)
    return {"blocks": blocks, "styles": result_styles, "conflicts": conflicts, "notes": notes}
