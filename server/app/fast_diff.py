"""测试工程师 N7：长文字的字符级对齐。difflib.SequenceMatcher 在两段都很长且中间不同时是二次方
（2 万字约 47s，99 万字拖住整个服务）。这里先去公共前后缀；中间不长（≤ EXACT_LIMIT 个元素）时仍用精确比对，
否则线性贪心对齐：在旧序列里向后最多看 WINDOW 个元素找同一元素，找不到算插入，跳过的旧元素算删除。
只用于“把原稿样式映射到新文字上”，对齐不精确只影响个别字的样式，不影响文字本身。"""

from collections.abc import Sequence
from difflib import SequenceMatcher

EXACT_LIMIT = 2000
WINDOW = 64

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
        if len(a) + len(b) <= limit:
            ops += [(op, i1 + head, i2 + head, j1 + head, j2 + head)
                    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes()]
        else:
            ops += _greedy(a, b, head)
    if tail:
        ops.append(("equal", n - tail, n, m - tail, m))
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
