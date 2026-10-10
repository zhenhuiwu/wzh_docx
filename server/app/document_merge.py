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

    @property
    def markdown(self) -> str:
        return assemble_entries(self.prefix, self.entries)


def _key(block: Block) -> tuple:
    """对齐用逐字节键（便宜）；是否“真的改了”再用规范键判断（_canon），只对差异区间计算。"""
    return (block.kind, block.body)


def _canon(blocks: list[Block]) -> list[tuple]:
    return [(block.kind, canonical_key(block.body)) for block in blocks]


def _raw(blocks: list[Block]) -> list[tuple]:
    """逐字节比较，同样带上节点类型。"""
    return [(block.kind, block.body) for block in blocks]


def _whitespace_only(a: list[Block], b: list[Block]) -> bool:
    """两段只差空白（空格数量、全角空格、换行等）。"""
    return _raw(a) != _raw(b) and [(x.kind, "".join(x.body.split())) for x in a] == [(x.kind, "".join(x.body.split())) for x in b]


def _dropped_whitespace(seg_b: list[Block], seg_t: list[Block], seg_m: list[Block]) -> int:
    """保留对方时，我方有几块“只改了空白”的修改没写入（三边块数相同时逐块数，否则整体算一处）。"""
    if len(seg_b) == len(seg_t) == len(seg_m):
        return sum(1 for b, t, m in zip(seg_b, seg_t, seg_m)
                   if _whitespace_only([m], [b]) and (m.kind, m.body) != (t.kind, t.body))
    return int(_whitespace_only(seg_m, seg_b) and _raw(seg_m) != _raw(seg_t))


def _boundaries(ops, n: int, m: int) -> tuple[dict[int, int], dict[int, int]]:
    """start[b]：base 位置 b 在另一侧对应的下标（位于 b 处插入之后）；end[b]：位于插入之前。"""
    start: dict[int, int] = {}
    end: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
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
    return [(i1, i2) for tag, i1, i2, _j1, _j2 in ops if tag != "equal"]


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


def merge3(base_md: str, theirs_md: str, mine_md: str, *, dedupe_inserts: bool = False) -> MergeResult:
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
    ops_t = difflib.SequenceMatcher(None, kb, kt, autojunk=False).get_opcodes()
    ops_m = difflib.SequenceMatcher(None, kb, km, autojunk=False).get_opcodes()
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
            if "m" in sides:
                result.whitespace_dropped += _dropped_whitespace(seg_b, seg_t, seg_m)  # 对方也改了这一处：保留对方并提示
            copy_theirs(t2)
            continue
        if not theirs_changed_here:
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
            reason = "MODIFIED"
        elif len(seg_b) == 1 and (not seg_t or not seg_m):
            reason = "DELETED"
        else:
            reason = "STRUCTURE"
        result.conflicts.append(Conflict(reason, seg_b, seg_t, seg_m, (lo, hi), (t1, t2), len(result.entries)))
        copy_theirs(t2)
    copy_theirs(len(theirs))
    return result
