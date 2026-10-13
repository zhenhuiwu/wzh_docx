"""审查员 M3：相似段落（模板化）时，位置配对必须是全局唯一最优才拆开；否则整块冲突。

fuzz 按身份跟踪 mine 的改动：被拆开的块里不能出现「跨位且文字上看得出」的错配（危险）。
复现原始问题：/tmp/p9-shift2.py、/tmp/p9-pairfuzz.py（审查员）。
"""

import collections
import difflib
import random

from app.document_merge import _key, _pairable, merge3, split

CH = "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处理府研"


def _ratio(a, b):
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _sent(r, n=None):
    return "".join(r.choice(CH) for _ in range(n or r.randint(6, 30))) + "。"


def _small_edit(r, t):
    t = t[:-1]
    k = max(1, len(t) // 6)
    p = r.randint(0, len(t))
    op = r.random()
    if op < 0.5:
        t = t[:p] + _sent(r, k)[:-1] + t[p:]
    elif op < 0.8:
        t = t[:p] + t[p + k :]
    else:
        t = t[:p] + _sent(r, k)[:-1] + t[p + k :]
    return (t or "改") + "。"


def _gen(r, style):
    n = r.randint(4, 12)
    if style == "templ":
        return [(i, f"第{i + 1}段：原始内容 {i + 1}。") for i in range(n)]
    if style == "mixed":
        return [(i, r.choice([f"第{i + 1}段：原始内容 {i + 1}。", "无。", _sent(r)])) for i in range(n)]
    return [(i, _sent(r)) for i in range(n)]


def _mutate(r, base):
    out = []
    nid = 1000
    for bid, t in base:
        x = r.random()
        if x < 0.12:
            continue
        if x < 0.35:
            out.append((bid, _small_edit(r, t)))
        elif x < 0.45:
            out.append((bid, _sent(r)))
        else:
            out.append((bid, t))
        if r.random() < 0.12:
            nid += 1
            out.append((f"n{nid}", r.choice([_sent(r), f"第{r.randint(20, 99)}段：原始内容 {r.randint(20, 99)}。", "无。"])))
    if r.random() < 0.1:
        out.insert(0, ("n0", _sent(r)))
    return out


def test_pairable_fuzz_has_no_visible_cross_mismatch():
    stats = collections.Counter()
    dangerous = []
    for seed in range(20000):
        r = random.Random(seed)
        style = r.choice(["rand", "templ", "mixed"])
        base = _gen(r, style)
        mine = _mutate(r, base)
        if [t for _, t in base] == [t for _, t in mine]:
            continue
        _, bb = split("\n\n".join(t for _, t in base))
        _, mb = split("\n\n".join(t for _, t in mine))
        if len(bb) != len(base) or len(mb) != len(mine):
            continue
        ops = difflib.SequenceMatcher(None, [_key(b) for b in bb], [_key(b) for b in mb], autojunk=False).get_opcodes()
        for (tag0, i1, i2, j1, j2), (tag, *_rest) in zip(ops, _pairable(ops, bb, mb)):
            if tag0 != "replace" or i2 - i1 != j2 - j1 or i2 - i1 < 2 or tag != "replace":
                continue
            stats["split"] += 1
            ids_a = [base[i1 + k][0] for k in range(i2 - i1)]
            a_t = [b.body for b in bb[i1:i2]]
            m_t = [b.body for b in mb[j1:j2]]
            for k in range(i2 - i1):
                mid = mine[j1 + k][0]
                if mid in ids_a and mid != ids_a[k] and _ratio(a_t[ids_a.index(mid)], m_t[k]) > max(0.5, _ratio(a_t[k], m_t[k])):
                    dangerous.append((seed, a_t, m_t))
                    break
    assert stats["split"] > 1000  # fuzz 确实覆盖到了拆开路径
    assert dangerous == [], dangerous[:3]


def test_shifted_similar_paragraphs_become_whole_conflict():
    """p9-shift2：在 8 后插 54、改 9、删 10；张改 9。不能把「我改的 9」配到「54」的位置上。"""
    base = ["第8段：原始内容 8。", "第9段：原始内容 9。", "第10段：原始内容 10。", "第11段：原始内容 11。"]
    mine = ["第8段：原始内容 8。", "第54段：原始内容 54。", "第9段：原始内容 9（我改）。", "第11段：原始内容 11。"]
    theirs = ["第8段：原始内容 8。", "第9段：原始内容 9（张改）。", "第10段：原始内容 10。", "第11段：原始内容 11。"]
    result = merge3("\n\n".join(base), "\n\n".join(theirs), "\n\n".join(mine))
    assert result.conflicts
    joined_mine = ["\n\n".join(b.body for b in c.mine) for c in result.conflicts]
    # 我的「第9段（我改）」必须和张改的第9段出现在同一个冲突里，而不是被当成第10段的改写或被吞掉
    hit = [c for c in result.conflicts if any("9（我改）" in b.body for b in c.mine)]
    assert hit, joined_mine
    assert any("9（张改）" in b.body for b in hit[0].theirs)
    assert all("第54段" not in b.body for b in result.conflicts[0].theirs)
