"""测试工程师 N7：长文字的字符级对齐。difflib.SequenceMatcher 在两段都很长且中间不同时是二次方
（2 万字约 47s，99 万字拖住整个服务）。这里先去公共前后缀；中间不长（≤ EXACT_LIMIT 个元素）时仍用精确比对，
否则线性贪心对齐：在旧序列里向后最多看 WINDOW 个元素找同一元素，找不到算插入，跳过的旧元素算删除。
只用于“把原稿样式映射到新文字上”，对齐不精确只影响个别字的样式，不影响文字本身。

代码审查 M8：保存路径（_check_word → edit_original_docx）也用它，贪心对齐在中间部分很长时会让未改动的字错配样式。
现在：规模不至于超时（两边长度之积 ≤ EXACT_CELLS）时仍用 SequenceMatcher；更大时先按两边都只出现一次的短片段对齐
（类似 patience diff：取 K 元片段→最长递增子序列→向两侧扩展），锚点之间的空隙递归处理，实在找不到锚点才退回贪心。"""

from collections.abc import Sequence
from difflib import SequenceMatcher

EXACT_LIMIT = 2000
EXACT_CELLS = 4_000_000  # len(a) * len(b) 不超过它时用 SequenceMatcher（约 2000×2000，最坏也在百毫秒级）
WINDOW = 64
ANCHOR_SIZES = (16, 8, 4)
SHORT_ANCHOR_LIMIT = 50_000

Opcode = tuple[str, int, int, int, int]


def bounded_opcodes(old: Sequence, new: Sequence, exact_limit: int | None = None) -> list[Opcode]:
    limit = EXACT_LIMIT if exact_limit is None else exact_limit
    n, m = len(old), len(new)
    head = 0
    while head < n and head < m and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < n - head and tail < m - head and old[n - 1 - tail] == new[m - 1 - tail]:
        tail += 1
    ops: list[Opcode] = []
    if head:
        ops.append(("equal", 0, head, 0, head))
    a, b = old[head:n - tail], new[head:m - tail]
    if len(a) or len(b):
        ops += _middle(a, b, head, limit if exact_limit is not None else None)
    if tail:
        ops.append(("equal", n - tail, n, m - tail, m))
    return ops


def _exact(a: Sequence, b: Sequence, offset: int) -> list[Opcode]:
    return [(op, i1 + offset, i2 + offset, j1 + offset, j2 + offset)
            for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes()]


def _small(a: Sequence, b: Sequence, limit: int | None) -> bool:
    if limit is not None:  # 显式上限（预览 / 测试）：按总长
        return len(a) + len(b) <= limit
    return len(a) + len(b) <= EXACT_LIMIT or len(a) * len(b) <= EXACT_CELLS


def _middle(a: Sequence, b: Sequence, offset: int, limit: int | None, depth: int = 0) -> list[Opcode]:
    if not len(a) or not len(b):
        if len(a):
            return [("delete", offset, offset + len(a), offset, offset)]
        return [("insert", offset, offset, offset, offset + len(b))] if len(b) else []
    if _small(a, b, limit):
        return _exact(a, b, offset)
    anchors: list[tuple[int, int, int]] = []
    # 很长且找不到长锚点（几乎整段不同）时不再逐级试短片段——那样 99 万字要几秒；直接贪心
    sizes = ANCHOR_SIZES if max(len(a), len(b)) <= SHORT_ANCHOR_LIMIT else ANCHOR_SIZES[:1]
    for size in sizes:
        if min(len(a), len(b)) >= size * 2:
            anchors = _anchors(a, b, size)
            if anchors:
                break
    if not anchors or depth > 32:
        return _greedy(a, b, offset)
    return _stitch(a, b, offset, anchors, limit, depth)


def _anchors(a: Sequence, b: Sequence, size: int) -> list[tuple[int, int, int]]:
    """两边都只出现一次的 size 元片段 → 按 b 顺序取 a 位置的最长递增子序列 → 合并相邻并向两侧扩展成 (i, j, 长度)。"""
    def grams(seq: Sequence) -> dict:
        seen: dict = {}
        tup = not isinstance(seq, str)
        for index in range(len(seq) - size + 1):
            key = tuple(seq[index:index + size]) if tup else seq[index:index + size]
            seen[key] = -1 if key in seen else index
        return seen
    in_a, in_b = grams(a), grams(b)
    pairs = sorted((j, in_a[key]) for key, j in in_b.items() if j >= 0 and in_a.get(key, -1) >= 0)
    if not pairs:
        return []
    # 最长递增子序列（按 a 位置），O(k log k)
    from bisect import bisect_left
    tails: list[int] = []
    tails_at: list[int] = []
    back = [-1] * len(pairs)
    for position, (_j, i) in enumerate(pairs):
        slot = bisect_left(tails, i)
        if slot == len(tails):
            tails.append(i)
            tails_at.append(position)
        else:
            tails[slot] = i
            tails_at[slot] = position
        back[position] = tails_at[slot - 1] if slot else -1
    chain: list[tuple[int, int]] = []
    position = tails_at[-1]
    while position >= 0:
        chain.append((pairs[position][1], pairs[position][0]))
        position = back[position]
    chain.reverse()
    runs: list[tuple[int, int, int]] = []
    for i, j in chain:
        if runs:
            pi, pj, length = runs[-1]
            if i - pi == j - pj and i <= pi + length:
                runs[-1] = (pi, pj, max(length, i - pi + size))
                continue
            if i < pi + length or j < pj + length:
                continue
        runs.append((i, j, size))
    out: list[tuple[int, int, int]] = []
    floor_a = floor_b = 0
    for index, (i, j, length) in enumerate(runs):
        ceil_a = runs[index + 1][0] if index + 1 < len(runs) else len(a)
        ceil_b = runs[index + 1][1] if index + 1 < len(runs) else len(b)
        while i > floor_a and j > floor_b and a[i - 1] == b[j - 1]:
            i, j, length = i - 1, j - 1, length + 1
        while i + length < ceil_a and j + length < ceil_b and a[i + length] == b[j + length]:
            length += 1
        out.append((i, j, length))
        floor_a, floor_b = i + length, j + length
    return out


def _stitch(a: Sequence, b: Sequence, offset: int, anchors: list[tuple[int, int, int]], limit: int | None, depth: int) -> list[Opcode]:
    ops: list[Opcode] = []
    pa = pb = 0
    for i, j, length in anchors + [(len(a), len(b), 0)]:
        if i > pa or j > pb:
            for op, i1, i2, j1, j2 in _middle(a[pa:i], b[pb:j], 0, limit, depth + 1):
                ops.append((op, i1 + pa + offset, i2 + pa + offset, j1 + pb + offset, j2 + pb + offset))
        if length:
            ops.append(("equal", i + offset, i + length + offset, j + offset, j + length + offset))
        pa, pb = i + length, j + length
    return ops


def _find(a: Sequence, item, start: int) -> int:
    if isinstance(a, str):
        return a.find(item, start, start + WINDOW)
    for index in range(start, min(len(a), start + WINDOW)):
        if a[index] == item:
            return index
    return -1


def _greedy(a: Sequence, b: Sequence, offset: int) -> list[Opcode]:
    ops: list[Opcode] = []

    def push(op: str, i1: int, i2: int, j1: int, j2: int) -> None:
        i1, i2, j1, j2 = i1 + offset, i2 + offset, j1 + offset, j2 + offset
        if ops and ops[-1][0] == op and ops[-1][2] == i1 and ops[-1][4] == j1:
            ops[-1] = (op, ops[-1][1], i2, ops[-1][3], j2)
        else:
            ops.append((op, i1, i2, j1, j2))

    i = 0
    for j, item in enumerate(b):
        found = _find(a, item, i) if i < len(a) else -1
        if found < 0:
            push("insert", i, i, j, j + 1)
            continue
        if found > i:
            push("delete", i, found, j, j)
        push("equal", found, found + 1, j, j + 1)
        i = found + 1
    if i < len(a):
        push("delete", i, len(a), len(b), len(b))
    return ops
