"""Bind an editor selection to Markdown source blocks, without text search.

Offsets are JavaScript UTF-16 code units of the decorated block's textContent.
Only a proven source/render mapping is selectable. Unsupported Markdown keeps
rendering normally, but receives no selection ID.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from hashlib import sha256
from html import escape, unescape
import re
from typing import Mapping

from lxml import html as html_dom
import markdown as markdown_renderer


class SelectionError(ValueError):
    """The supplied selection cannot be safely applied to the source snapshot."""


@dataclass(frozen=True)
class _Format:
    kind: str
    opening: str
    closing: str
    start: int
    close_start: int
    end: int


@dataclass(frozen=True)
class _Char:
    value: str
    start: int
    end: int
    styles: tuple[_Format, ...] = ()


@dataclass
class _Block:
    block_id: str
    start: int
    end: int
    body_start: int
    body: str
    chars: list[_Char] | None
    signature: tuple
    node_index: int = -1


_LIST = re.compile(r"^( {0,3})(?:[-+*]|\d+\.)[ \t]+")
_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")
_LINK = re.compile(r"\[([^\[\]\n]+)\]\(([^()\n]*)\)")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_ESCAPABLE = frozenset("\\`*_{}[]()>#+-.!")
_EXCLUDED = frozenset({"table", "thead", "tbody", "tr", "td", "th", "blockquote", "pre", "script", "style"})
_EXCLUDED_ANCESTORS = _EXCLUDED | {"h1", "h2", "h3", "h4", "h5", "h6"}
_SEMANTIC = {"strong": "strong", "em": "em", "code": "code", "a": "a"}


def _render(value: str) -> str:
    return markdown_renderer.markdown(value, extensions=["tables", "fenced_code"])


def _root(value: str):
    root = html_dom.fragment_fromstring(value or "", create_parent="div")
    # Markdown writes a newline after <br>. Native Word HTML sometimes does not.
    # Make textContent identical for both previews; do not count <br> a second time.
    for br in root.iter("br"):
        if not (br.tail or "").startswith("\n"):
            br.tail = "\n" + (br.tail or "")
    return root


def _nodes(root) -> list:
    result = []
    for node in root.iter():
        if node.tag not in {"p", "li"}:
            continue
        if any(parent.tag in _EXCLUDED_ANCESTORS for parent in node.iterancestors()):
            continue
        if any(child.tag in {"p", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"} for child in node.iterdescendants()):
            continue
        if "".join(node.itertext()):
            result.append(node)
    return result


def _signature(node) -> tuple:
    chars = []

    def walk(current, styles=()):
        kind = _SEMANTIC.get(current.tag)
        if kind:
            attributes = tuple((name, current.get(name) or "") for name in ("href", "title")) if kind == "a" else ()
            styles += ((kind, attributes),)
        if current.text:
            chars.extend((char, styles) for char in current.text)
        for child in current:
            if child.tag != "br":
                walk(child, styles)
            if child.tail:
                chars.extend((char, styles) for char in child.tail)

    walk(node)
    return tuple(chars)


def _simple_signature(chars: list[_Char]) -> tuple:
    return tuple((char.value, tuple(style.kind for style in char.styles)) for char in chars)


def _close_marker(value: str, marker: str, start: int, end: int) -> int:
    position = value.find(marker, start, end)
    while position >= 0:
        escapes = 0
        cursor = position - 1
        while cursor >= 0 and value[cursor] == "\\":
            escapes += 1
            cursor -= 1
        if escapes % 2 == 0:
            return position
        position = value.find(marker, position + len(marker), end)
    return -1


def _inline(value: str, absolute: int) -> list[_Char]:
    """Parse a small inline subset and subsequently prove it against Markdown."""

    def parse(start: int, end: int, styles=()) -> list[_Char]:
        result: list[_Char] = []
        index = start
        while index < end:
            char = value[index]
            if char == "\\" and index + 1 < end and value[index + 1] in _ESCAPABLE:
                result.append(_Char(value[index + 1], absolute + index, absolute + index + 2, styles))
                index += 2
                continue
            if char == "&":
                entity = _ENTITY.match(value, index, end)
                decoded = unescape(entity.group()) if entity else ""
                if entity and decoded != entity.group():
                    if len(decoded) != 1:
                        # Several characters would share one source span, so an
                        # offset inside them cannot be mapped back unambiguously.
                        raise SelectionError("此段含有无法安全定位的字符实体。")
                    result.append(_Char(decoded, absolute + index, absolute + entity.end(), styles))
                    index = entity.end()
                    continue
            if char == "<":
                br = _BR.match(value, index, end)
                if br:
                    next_index = br.end()
                    if next_index < end and value[next_index] == "\n":
                        next_index += 1
                    result.append(_Char("\n", absolute + index, absolute + next_index, styles))
                    index = next_index
                    continue
                if re.match(r"</?[A-Za-z!]|<!--", value[index:end]):
                    raise SelectionError("此段含有无法安全定位的 HTML 内容。")
            if value.startswith("![", index):
                raise SelectionError("选区所在段落含有图片，不能安全替换。")
            if char == "[":
                link = _LINK.match(value, index, end)
                if link:
                    closing_start = index + 1 + len(link.group(1))
                    style = _Format("a", "[", value[closing_start:link.end()], absolute + index,
                                    absolute + closing_start, absolute + link.end())
                    result.extend(parse(index + 1, closing_start, styles + (style,)))
                    index = link.end()
                    continue
            if char == "`":
                opening_end = index + 1
                while opening_end < end and value[opening_end] == "`":
                    opening_end += 1
                marker = value[index:opening_end]
                closing = _close_marker(value, marker, opening_end, end)
                if closing >= 0:
                    body_start, body_end = opening_end, closing
                    while body_start < body_end and value[body_start] == " ":
                        body_start += 1
                    while body_end > body_start and value[body_end - 1] == " ":
                        body_end -= 1
                    style = _Format("code", marker, marker, absolute + index,
                                    absolute + closing, absolute + closing + len(marker))
                    result.extend(_Char(value[position], absolute + position, absolute + position + 1,
                                        styles + (style,)) for position in range(body_start, body_end))
                    index = closing + len(marker)
                    continue
            if char in {"*", "_"}:
                marker = char * (2 if value.startswith(char * 2, index) else 1)
                closing = _close_marker(value, marker, index + len(marker), end)
                if closing > index + len(marker) and not value[index + len(marker)].isspace() and not value[closing - 1].isspace():
                    style = _Format("strong" if len(marker) == 2 else "em", marker, marker,
                                    absolute + index, absolute + closing, absolute + closing + len(marker))
                    result.extend(parse(index + len(marker), closing, styles + (style,)))
                    index = closing + len(marker)
                    continue
            if char in "\r\n":
                spaces = 0
                while result and result[-1].value == " " and result[-1].end == absolute + index - spaces:
                    spaces += 1
                    result.pop()
                if spaces < 2:
                    result.extend(_Char(" ", absolute + index - spaces + position,
                                        absolute + index - spaces + position + 1, styles)
                                  for position in range(spaces))
                    spaces = 0
                newline_size = 2 if char == "\r" and index + 1 < end and value[index + 1] == "\n" else 1
                result.append(_Char("\n", absolute + index - spaces, absolute + index + newline_size, styles))
                index += newline_size - 1
            else:
                result.append(_Char(char, absolute + index, absolute + index + 1, styles))
            index += 1
        return result

    return parse(0, len(value))


def _source_blocks(source: str) -> list[_Block]:
    lines = source.splitlines(keepends=True)
    offsets, total = [], 0
    for line in lines:
        offsets.append(total)
        total += len(line)
    candidates: list[_Block] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip("\r\n")
        if not stripped.strip():
            index += 1
            continue
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)
            index += 1
            while index < len(lines) and not re.match(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$", lines[index]):
                index += 1
            index += 1
            continue
        if _HEADING.match(stripped) or stripped.startswith("    ") or stripped.startswith("\t"):
            index += 1
            continue
        if stripped.lstrip().startswith(">"):
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        if index + 1 < len(lines) and _SETEXT.match(lines[index + 1].rstrip("\r\n")):
            index += 2
            continue
        if index + 1 < len(lines) and _TABLE_RULE.match(lines[index + 1].rstrip("\r\n")):
            index += 2
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        start_line = index
        listing = _LIST.match(stripped)
        index += 1
        while index < len(lines) and lines[index].strip():
            if _HEADING.match(lines[index]) or _LIST.match(lines[index]) or re.match(r"^ {0,3}(`{3,}|~{3,})", lines[index]):
                break
            index += 1
        start = offsets[start_line]
        end = offsets[index] if index < len(lines) else len(source)
        raw = source[start:end].rstrip("\r\n")
        end = start + len(raw)
        prefix = listing.end() if listing else len(raw) - len(raw.lstrip(" "))
        body_start = start + prefix
        body = source[body_start:end]
        rendered_nodes = _nodes(_root(_render(body)))
        for node in rendered_nodes:
            signature = _signature(node)
            chars = None
            if len(rendered_nodes) == 1:
                try:
                    parsed = _inline(body, body_start)
                    simplified = tuple((char, tuple(kind for kind, _attributes in styles)) for char, styles in signature)
                    if _simple_signature(parsed) == simplified:
                        chars = parsed
                except SelectionError:
                    pass
            identifier = f"selection-{body_start:x}-{sha256(source[start:end].encode('utf-8')).hexdigest()[:16]}"
            candidates.append(_Block(identifier, start, end, body_start, body, chars, signature))
    return candidates


def _align(candidates: list[_Block], nodes: list) -> list[tuple[int, int]]:
    left = [block.signature for block in candidates]
    right = [_signature(node) for node in nodes]
    if len(left) == len(right) and left == right:
        return list(enumerate(range(len(left))))
    # When unsupported structure introduces gaps, only forced LCS pairs are safe.
    # Ambiguous repeated paragraphs remain unselectable instead of binding wrongly.
    n, m = len(left), len(right)
    if not n or not m or n * m > 4_000_000:
        return []
    forward = [array("H", [0]) * (m + 1) for _ in range(n + 1)]
    backward = [array("H", [0]) * (m + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(m):
            forward[i + 1][j + 1] = forward[i][j] + 1 if left[i] == right[j] else max(forward[i][j + 1], forward[i + 1][j])
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            backward[i][j] = backward[i + 1][j + 1] + 1 if left[i] == right[j] else max(backward[i + 1][j], backward[i][j + 1])
    length = forward[n][m]
    result = []
    for i in range(n):
        if max(forward[i][j] + backward[i + 1][j] for j in range(m + 1)) == length:
            continue
        possible = [j for j in range(m) if left[i] == right[j] and forward[i][j] + 1 + backward[i + 1][j + 1] == length]
        if len(possible) == 1:
            result.append((i, possible[0]))
    return result


def _mapped_blocks(source: str) -> tuple[list[_Block], list]:
    nodes = _nodes(_root(_render(source)))
    candidates = _source_blocks(source)
    mapped = []
    for candidate, node_index in _align(candidates, nodes):
        block = candidates[candidate]
        if block.chars:
            block.node_index = node_index
            mapped.append(block)
    return mapped, nodes


def selection_blocks(source: str) -> list[dict]:
    """Return the supported blocks, in document order, for a source snapshot."""
    blocks, _nodes_ = _mapped_blocks(source)
    return [{"blockId": block.block_id, "text": "".join(char.value for char in block.chars or [])}
            for block in blocks]


def decorate_selection_html(source: str, html: str | None = None) -> str:
    """Add stable IDs to proven p/li blocks of Markdown or a styled preview.

    Styled HTML must retain the Markdown block order and inline text. Font spans
    are allowed. If its semantic text differs, no IDs are emitted for that HTML.
    """
    rendered = _render(source) if html is None else html
    root = _root(rendered)
    for node in root.iter():
        node.attrib.pop("data-selection-id", None)
    blocks, markdown_nodes = _mapped_blocks(source)
    nodes = _nodes(root)
    if [_signature(node) for node in nodes] == [_signature(node) for node in markdown_nodes]:
        for block in blocks:
            nodes[block.node_index].set("data-selection-id", block.block_id)
    return escape(root.text or "", quote=False) + "".join(html_dom.tostring(child, encoding="unicode", method="html") for child in root)


def _codepoint_offset(value: str, units: object) -> int:
    if type(units) is not int or units < 0:
        raise SelectionError("选区偏移不正确。")
    current = 0
    for index, char in enumerate(value):
        if current == units:
            return index
        current += 2 if ord(char) > 0xFFFF else 1
        if current > units:
            raise SelectionError("选区不能拆开一个 Unicode 字符。")
    if current == units:
        return len(value)
    raise SelectionError("选区已超出正文范围。")


def validate_selection(source: str, selection: Mapping[str, object]) -> str:
    """Validate against this source snapshot and return its exact selected text."""
    _blocks, _start, _end, text = _resolve(source, selection)
    return text


def _resolve(source: str, selection: Mapping[str, object]):
    if (not isinstance(selection, Mapping) or not {"blockId", "start", "end", "text"} <= set(selection)
            or set(selection) - {"blockId", "endBlockId", "start", "end", "text"}):
        raise SelectionError("请重新选择正文文字。")
    identifier, exact = selection["blockId"], selection["text"]
    last_identifier = selection.get("endBlockId")
    if last_identifier is None:
        last_identifier = identifier
    if not isinstance(identifier, str) or not isinstance(exact, str) or not exact.strip():
        raise SelectionError("选区不能为空。")
    if not isinstance(last_identifier, str) or not last_identifier:
        raise SelectionError("选区结束段落不正确。")
    blocks, nodes = _mapped_blocks(source)
    first = next((index for index, item in enumerate(blocks) if item.block_id == identifier), None)
    last = next((index for index, item in enumerate(blocks) if item.block_id == last_identifier), None)
    if first is None or last is None or last < first:
        raise SelectionError("选区所在段落已变化或格式暂不支持，请重新选择。")
    selected = blocks[first:last + 1]
    if len(selected) > 1 and (any(nodes[block.node_index].tag != "p" for block in selected)
            or any(right.node_index != left.node_index + 1 or source[left.end:right.start].strip()
                   for left, right in zip(selected, selected[1:]))):
        raise SelectionError("请只选择连续的正文段落，选区不能包含标题、表格、图片或列表。")
    texts = ["".join(char.value for char in block.chars or []) for block in selected]
    start = _codepoint_offset(texts[0], selection["start"])
    end = _codepoint_offset(texts[-1], selection["end"])
    text = texts[0][start:end] if len(selected) == 1 else "\n\n".join([texts[0][start:], *texts[1:-1], texts[-1][:end]])
    if start >= len(texts[0]) or end == 0 or (len(selected) == 1 and start >= end) or text != exact:
        raise SelectionError("选中文字已变化，请重新选择并生成。")
    first_chars, last_chars = selected[0].chars, selected[-1].chars
    assert first_chars and last_chars
    if (start and first_chars[start - 1].start == first_chars[start].start
            or end < len(last_chars) and last_chars[end - 1].end == last_chars[end].end):
        raise SelectionError("选区不能拆开一个编码字符。")
    return selected, start, end, exact


def _plain_source(value: str) -> str:
    parts = []
    for char in value:
        if char == "\n":
            # A literal <br> keeps even consecutive blank lines inside one block.
            parts.append("<br />\n")
        elif char == "&":
            parts.append("&amp;")
        elif char == "<":
            parts.append("&lt;")
        elif char == ">":
            parts.append("&gt;")
        elif char in _ESCAPABLE:
            parts.append("\\" + char)
        else:
            parts.append(char)
    return "".join(parts)


def replace_selection(source: str, selection: Mapping[str, object], generated_text: str) -> str:
    """Replace one bound range, preserving unselected text and inline formatting."""
    blocks, start, end, _exact = _resolve(source, selection)
    block, last_block = blocks[0], blocks[-1]
    if not isinstance(generated_text, str) or not generated_text.strip() or len(generated_text) > 20000:
        raise SelectionError("生成正文不能为空，且不能超过 20000 字。")
    if "\r" in generated_text:
        generated_text = generated_text.replace("\r\n", "\n").replace("\r", "\n")
    if any(ord(char) < 32 and char not in "\n\t" or 0xD800 <= ord(char) <= 0xDFFF for char in generated_text):
        raise SelectionError("生成正文含有不支持的控制字符。")
    chars, last_chars = block.chars, last_block.chars
    assert chars is not None and last_chars is not None
    first, last = chars[start], last_chars[end - 1]
    source_start, source_end = first.start, last.end
    start_styles, end_styles = first.styles, list(last.styles)
    while end_styles:
        ending = end_styles[-1]
        if any(ending in char.styles for char in last_chars[end:]):
            break
        source_end = max(source_end, ending.end)
        end_styles.pop()
    common = 0
    while common < min(len(start_styles), len(end_styles)) and start_styles[common] == end_styles[common]:
        common += 1
    parts = re.split(r"\n[ \t]*\n+", generated_text) if len(blocks) > 1 else [generated_text]
    if any(not part.strip() for part in parts):
        raise SelectionError("生成正文包含空段落，请重新生成。")
    if any(style.kind == "code" for style in start_styles):
        closing = next(style.closing for style in start_styles if style.kind == "code")
        if closing in generated_text:
            raise SelectionError("代码选区的生成正文不能含有代码分隔符。")
        escaped_parts = parts
    else:
        escaped_parts = [_plain_source(part) for part in parts]
    paragraph_break = "".join(style.closing for style in reversed(start_styles)) + "\n\n"
    paragraph_break += "".join(style.opening for style in start_styles)
    replacement = paragraph_break.join(escaped_parts)
    repair = "".join(style.closing for style in reversed(start_styles[common:]))
    repair += "".join(style.opening for style in end_styles[common:])
    updated = source[:source_start] + replacement + repair + source[source_end:]
    before_nodes = _nodes(_root(_render(source)))
    after_nodes = _nodes(_root(_render(updated)))
    signatures = [_signature(node) for node in before_nodes]
    old_signature = signatures[block.node_index]
    start_style = old_signature[start][1]
    replacement_signatures = [tuple((char, start_style) for char in part) for part in parts]
    replacement_signatures[0] = old_signature[:start] + replacement_signatures[0]
    replacement_signatures[-1] += signatures[last_block.node_index][end:]
    signatures[block.node_index:last_block.node_index + 1] = replacement_signatures
    if signatures != [_signature(node) for node in after_nodes]:
        raise SelectionError("这次改写无法保留未选文字的格式，请缩小选区后重试。")
    return updated
