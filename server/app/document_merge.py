"""整篇入口的块级三方合并（01-阶段2设计 §5.2 整篇规则 1–7）。纯计算，不访问数据库。"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from app.markdown_blocks import Block, canonical_key, split


@dataclass
class Entry:
    block: Block
    source: str            # "t" theirs / "m" mine / "n" 新插入（处理冲突时）
    index: int


@dataclass
class Conflict:
    reason: str            # MODIFIED / DELETED / STRUCTURE
    base: list[Block]
    theirs: list[Block]
    mine: list[Block]
    base_range: tuple[int, int]
    theirs_range: tuple[int, int]
    out_index: int         # 在合并结果（冲突处取对方版本）中的起点


@dataclass
class MergeResult:
    prefix: str
    entries: list[Entry]
    conflicts: list[Conflict] = field(default_factory=list)
    theirs_changed: bool = False
    prefix_conflict: bool = False
    whitespace_dropped: int = 0   # 我方只改了空白、因对方也改了这一处而没写入的块数（合并提示用）
    whitespace_deleted: int = 0   # 我方只改了空白、但对方删掉了这一块
    theirs_whitespace_overridden: int = 0   # 对方只改了空白、我方改了这一块的文字（采用我方，对方的空白修改没保留）
    table_rows_merged: int = 0   # 按行合并的表格数（设计师复验 2 C：合并后 Word 校验失败时由调用方关掉按行合并重来）

    def whitespace_notes(self) -> dict[str, int]:
        """合并提示用的计数（只返回非零项，键名即接口字段名）。"""
        notes = {"whitespaceDropped": self.whitespace_dropped, "whitespaceDeleted": self.whitespace_deleted,
                 "theirsWhitespaceOverridden": self.theirs_whitespace_overridden}
        return {key: value for key, value in notes.items() if value}

    @property
    def markdown(self) -> str:
        return assemble_entries(self.prefix, self.entries)


def _key(block: Block) -> tuple:
    """对齐用逐字节键（便宜）；是否“真的改了”再用规范键判断（_canon），只对差异区间计算。"""
    return (block.kind, block.body)


def _canon(blocks: list[Block]) -> list[tuple]:
    # 终审建议：代码块里的缩进和空白有语义，按原文比较（双方都改了缩进且不同 → 进冲突，不再只提示）
    return [(block.kind, block.body if block.kind == "code" else canonical_key(block.body)) for block in blocks]


def _raw(blocks: list[Block]) -> list[tuple]:
    """逐字节比较，同样带上节点类型。"""
    return [(block.kind, block.body) for block in blocks]


def _whitespace_only(a: list[Block], b: list[Block]) -> bool:
    """两段只差空白（空格数量、全角空格、换行等）。代码块的空白是实质修改，不算。"""
    if any(block.kind == "code" for block in [*a, *b]):
        return False
    return _raw(a) != _raw(b) and [(x.kind, "".join(x.body.split())) for x in a] == [(x.kind, "".join(x.body.split())) for x in b]


def _dropped_whitespace(seg_b: list[Block], seg_t: list[Block], seg_m: list[Block]) -> int:
    """保留对方时，我方有几块“只改了空白”的修改没写入（三边块数相同时逐块数，否则整体算一处）。"""
    if len(seg_b) == len(seg_t) == len(seg_m):
        return sum(1 for b, t, m in zip(seg_b, seg_t, seg_m)
                   if _whitespace_only([m], [b]) and (m.kind, m.body) != (t.kind, t.body))
    return int(_whitespace_only(seg_m, seg_b) and _raw(seg_m) != _raw(seg_t))


SPLIT_SIMILARITY = 0.5


def _ratio(a: str, b: str) -> float:
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _upper(a: str, b: str) -> float:
    """相似度的廉价上界（用来剪枝）。"""
    if a == b:
        return 1.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return min(matcher.real_quick_ratio(), matcher.quick_ratio())


def _assignment_value(weight: list[list[float]], banned: tuple[int, int] | None = None) -> float:
    """最大权完全匹配的权值和（匈牙利算法，O(n^3)）。banned=(i, j) 时禁止这一对。"""
    size = len(weight)
    big = 1e9
    cost = [[(big if banned == (i, j) else -weight[i][j]) for j in range(size)] for i in range(size)]
    u, v = [0.0] * (size + 1), [0.0] * (size + 1)
    match, way = [0] * (size + 1), [0] * (size + 1)
    for i in range(1, size + 1):
        match[0] = i
        j0 = 0
        minv, used = [float("inf")] * (size + 1), [False] * (size + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = match[j0], float("inf"), 0
            for j in range(1, size + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(size + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if match[j0] == 0:
                break
        while True:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1
            if j0 == 0:
                break
    total = 0.0
    for j in range(1, size + 1):
        c = cost[match[j] - 1][j - 1]
        if c >= big:
            return float("-inf")
        total -= c
    return total


def _positional_is_unique_best(a: list[str], b: list[str]) -> bool:
    """审查 M3：先用全局相似度做最优匹配。权重 = 相似度（< 0.5 记 0，不相似的交叉对不算匹配）。
    按位置配对必须是最大权匹配；且每个相似的位置对都必不可少（禁掉它后最优值严格下降），即没有打平的另一种配法。
    否则（如模板化段落里插入的新段比原位那段更像）不拆开，整块作为结构冲突。"""
    size = len(a)
    weight = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i == j or _upper(a[i], b[j]) >= SPLIT_SIMILARITY:
                score = _ratio(a[i], b[j])
                weight[i][j] = score if score >= SPLIT_SIMILARITY else 0.0
    identity = sum(weight[k][k] for k in range(size))
    # 每个新块都不能有比原位那块更像（或一样像）的别的旧块：否则它多半来自那一块，按位置配会错位
    for k in range(size):
        floor = max(weight[k][k], SPLIT_SIMILARITY)
        if any(weight[other][k] >= floor for other in range(size) if other != k):
            return False
    if not any(weight[i][j] for i in range(size) for j in range(size) if i != j):
        return True  # 交叉对都不相似：按位置配对是唯一合理的配法
    eps = 1e-9
    if _assignment_value(weight) > identity + eps:
        return False
    for k in range(size):
        if weight[k][k] and _assignment_value(weight, (k, k)) >= identity - eps:
            return False
    return True


# 审查 M5：精确判定（匈牙利最优匹配）最多 60 块（模板化最坏约 0.2s）；更大的等长替换区间不再按旧的逐对规则拆，
# 一律整块作为结构冲突——旧规则在模板化“整章修改 / 挪动粘贴”里会错配，重交后正文出现同一段的两个版本
PAIR_EXACT_LIMIT = 60


def _pairable(ops, a_blocks, b_blocks):
    """审查 N1 / M3：块数相同的替换按位置逐块拆开前先核对配对：按位置配对必须是全局唯一最优（见上），
    且每一对要么相似（≥ 0.5），要么是原位整段改写（与区间里别的块都不更像）。否则改记为 replace_whole，
    整块作为一个 hunk（结构冲突），不按位置错配。超大区间（> PAIR_EXACT_LIMIT 块）直接整块（审查 M5）。"""
    out = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "replace" and i2 - i1 == j2 - j1 > 1:
            size = i2 - i1
            a = [block.body for block in a_blocks[i1:i2]]
            b = [block.body for block in b_blocks[j1:j2]]
            if size > PAIR_EXACT_LIMIT or not _positional_is_unique_best(a, b):
                tag = "replace_whole"
        out.append((tag, i1, i2, j1, j2))
    return out


def _boundaries(ops, n: int, m: int) -> tuple[dict[int, int], dict[int, int]]:
    """start[b]：base 位置 b 在另一侧对应的下标（位于 b 处插入之后）；end[b]：位于插入之前。"""
    start: dict[int, int] = {}
    end: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal" or tag == "replace" and i2 - i1 == j2 - j1:
            for offset in range(i2 - i1):
                end.setdefault(i1 + offset, j1 + offset)
                start[i1 + offset] = j1 + offset
        elif tag == "insert":
            end.setdefault(i1, j1)
            start[i1] = j2
        else:
            end.setdefault(i1, j1)
            start[i1] = j1
    end.setdefault(n, m)
    start[n] = m
    return start, end


def _hunks(ops) -> list[tuple[int, int]]:
    """增量审查 M1：块数相同的连续替换逐块拆开——相邻两段一段只有我改、一段双方都改时，只有后者进冲突，
    前者（如自己已保存后续打的段）照常合并，不随相邻的冲突段进冲突副本。"""
    out: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "replace" and i2 - i1 == j2 - j1:
            out.extend((index, index + 1) for index in range(i1, i2))
        elif tag != "equal":
            out.append((i1, i2))
    return out


def _overlaps(lo: int, hi: int, b1: int, b2: int) -> bool:
    if lo == hi and b1 == b2:
        return lo == b1
    if lo == hi:
        return b1 < lo < b2
    if b1 == b2:
        return lo < b1 < hi
    return b1 < hi and lo < b2


def assemble_entries(prefix: str, entries: list[Entry]) -> str:
    """拼接；来源不相邻的两块之间补空行，避免段落粘连或被吸进列表（列表项之间保留单换行）。"""
    parts = [prefix]
    for index, entry in enumerate(entries):
        gap = entry.block.gap_after
        if index + 1 < len(entries):
            following = entries[index + 1]
            adjacent = following.source == entry.source and following.index == entry.index + 1 and entry.source != "n"
            if not adjacent:
                if entry.block.kind == following.block.kind == "list_item":
                    gap = gap if gap.endswith("\n") else gap + "\n"
                elif gap.count("\n") < 2:
                    gap = "\n\n" if not gap.strip(" \t") else gap + "\n" * (2 - gap.count("\n"))
        elif prefix == "" and not gap and len(entries) == 1:
            gap = entry.block.gap_after
        parts.append(entry.block.body + gap)
    return "".join(parts)


def _merge_table_rows(base: Block, theirs: Block, mine: Block) -> Block | None:
    """表格整块被双方都改了：行数不变、每一行至多一方改过（或双方改法相同）时按行合并；否则 None（仍是冲突）。
    测试工程师 table_other 偶发整表冲突：客户端平移不了（保存在途时续打等）退回按块合并，不同行的修改不该成冲突。"""
    if not (base.kind == theirs.kind == mine.kind == "table"):
        return None
    rows_b, rows_t, rows_m = (block.body.splitlines(keepends=True) for block in (base, theirs, mine))
    if not (len(rows_b) == len(rows_t) == len(rows_m)):
        return None
    merged: list[str] = []
    for row_b, row_t, row_m in zip(rows_b, rows_t, rows_m):
        if row_m == row_b or row_m == row_t:
            merged.append(row_t)
        elif row_t == row_b:
            merged.append(row_m)
        else:
            return None
    return Block(theirs.kind, "".join(merged), theirs.gap_after, theirs.heading_level)


def merge3(base_md: str, theirs_md: str, mine_md: str, *, dedupe_inserts: bool = False, table_rows: bool = True) -> MergeResult:
    """mine 相对 base 的修改合并到 theirs 上。冲突处在结果中保留 theirs，并逐处列出。"""
    base_prefix, base = split(base_md)
    theirs_prefix, theirs = split(theirs_md)
    mine_prefix, mine = split(mine_md)
    theirs_changed = theirs_md != base_md
    prefix_conflict = False
    if mine_prefix == base_prefix or mine_prefix == theirs_prefix:
        prefix = theirs_prefix
    elif theirs_prefix == base_prefix:
        prefix = mine_prefix
    else:
        prefix, prefix_conflict = theirs_prefix, mine_prefix.strip() != theirs_prefix.strip()
    if not theirs_changed:
        return MergeResult(mine_prefix, [Entry(block, "m", index) for index, block in enumerate(mine)])
    kb, kt, km = [_key(b) for b in base], [_key(b) for b in theirs], [_key(b) for b in mine]
    ops_t = _pairable(difflib.SequenceMatcher(None, kb, kt, autojunk=False).get_opcodes(), base, theirs)
    ops_m = _pairable(difflib.SequenceMatcher(None, kb, km, autojunk=False).get_opcodes(), base, mine)
    start_t, end_t = _boundaries(ops_t, len(base), len(theirs))
    start_m, end_m = _boundaries(ops_m, len(base), len(mine))
    hunks = sorted([(b1, b2, "t") for b1, b2 in _hunks(ops_t)] + [(b1, b2, "m") for b1, b2 in _hunks(ops_m)],
                   key=lambda item: (item[0], item[1] if item[1] > item[0] else item[0] - 0.5))
    groups: list[list[tuple[int, int, str]]] = []
    for hunk in hunks:
        if groups:
            lo = min(h[0] for h in groups[-1])
            hi = max(h[1] for h in groups[-1])
            if _overlaps(lo, hi, hunk[0], hunk[1]):
                groups[-1].append(hunk)
                continue
        groups.append([hunk])
    groups.sort(key=lambda g: (min(h[0] for h in g), 0 if all(h[0] == h[1] for h in g) else 1))

    result = MergeResult(prefix, [], theirs_changed=True, prefix_conflict=prefix_conflict)
    cursor = 0

    def copy_theirs(upto: int) -> None:
        nonlocal cursor
        for index in range(cursor, upto):
            result.entries.append(Entry(theirs[index], "t", index))
        cursor = max(cursor, upto)

    for group in groups:
        lo, hi = min(h[0] for h in group), max(h[1] for h in group)
        sides = {h[2] for h in group}
        if lo == hi:
            t1, t2, m1, m2 = end_t[lo], start_t[lo], end_m[lo], start_m[lo]
        else:
            t1, t2, m1, m2 = start_t[lo], end_t[hi], start_m[lo], end_m[hi]
        copy_theirs(t1)
        seg_t, seg_m, seg_b = theirs[t1:t2], mine[m1:m2], base[lo:hi]
        mine_changed = "m" in sides and _canon(seg_m) != _canon(seg_b)
        theirs_changed_here = "t" in sides and _canon(seg_t) != _canon(seg_b)
        if not mine_changed:
            # 规范键相同：只是写法差异（转义、列表符等）或只改了空白。
            if "m" in sides and "t" not in sides and _raw(seg_m) != _raw(seg_b):
                # 终验 S-1：对方没碰这一块，我方的字节（如只改空格数量）原样写入，不能被静默丢掉
                result.entries.extend(Entry(block, "m", m1 + offset) for offset, block in enumerate(seg_m))
                cursor = max(cursor, t2)
                continue
            if "m" in sides and not seg_t:
                result.whitespace_deleted += _dropped_whitespace(seg_b, seg_t, seg_m)  # 对方删掉了这一块：按删除处理并提示
            elif "m" in sides:
                result.whitespace_dropped += _dropped_whitespace(seg_b, seg_t, seg_m)  # 对方也改了这一处：保留对方并提示
            copy_theirs(t2)
            continue
        if not theirs_changed_here:
            if "t" in sides and _raw(seg_t) != _raw(seg_b):
                # 对方只改了空白、我方改了文字：采用我方，提示对方的空白调整没保留
                result.theirs_whitespace_overridden += sum(1 for b, t in zip(seg_b, seg_t) if _whitespace_only([t], [b])) \
                    if len(seg_b) == len(seg_t) else int(_whitespace_only(seg_t, seg_b))
            result.entries.extend(Entry(block, "m", m1 + offset) for offset, block in enumerate(seg_m))
            cursor = max(cursor, t2)
            continue
        same = _canon(seg_t) == _canon(seg_m)
        if lo == hi:
            # 双方在同一位置都只是插入：都保留、对方在前；内容相同且对方插入来自同一账号时去重（规则5）
            copy_theirs(t2)
            if not (same and dedupe_inserts):
                result.entries.extend(Entry(block, "m", m1 + offset) for offset, block in enumerate(seg_m))
            continue
        if same:
            if _whitespace_only(seg_m, seg_t):
                # 双方改法相同、只差空白：保留对方并提示
                result.whitespace_dropped += sum(1 for t, m in zip(seg_t, seg_m) if _whitespace_only([m], [t])) \
                    if len(seg_t) == len(seg_m) else 1
            copy_theirs(t2)
            continue
        if len(seg_b) == 1 and len(seg_t) == 1 and len(seg_m) == 1:
            rows = _merge_table_rows(seg_b[0], seg_t[0], seg_m[0]) if table_rows else None
            if rows is not None:
                result.table_rows_merged += 1
                result.entries.append(Entry(rows, "m", m1))
                cursor = max(cursor, t2)
                continue
            reason = "MODIFIED"
        elif len(seg_b) == 1 and (not seg_t or not seg_m):
            reason = "DELETED"
        else:
            reason = "STRUCTURE"
        result.conflicts.append(Conflict(reason, seg_b, seg_t, seg_m, (lo, hi), (t1, t2), len(result.entries)))
        copy_theirs(t2)
    copy_theirs(len(theirs))
    return result
