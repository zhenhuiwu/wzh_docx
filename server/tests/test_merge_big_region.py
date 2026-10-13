"""审查 M5：超过 40 块的等长替换区间曾按旧的逐对规则拆开，模板化“整章修改 / 挪动粘贴”会错配，
重交 appliable 后正文同时出现同一段的“张改”和“我改”（审查员 /tmp/p10-bigfuzz.py：793/3000）。
现在 ≤ 60 块用精确判定（全局唯一最优），> 60 块整块作为结构冲突。生成器与审查员 fuzz 相同（SEED=7 即其复现一）。
"""

import collections
import random
import time

import app.document_merge as M
from app.document_merge import merge3

CH = "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处理府研"


def sent(r, n=None):
    return "".join(r.choice(CH) for _ in range(n or r.randint(8, 30))) + "。"


def small_edit(r, t, tag):
    t = t[:-1]
    p = r.randint(0, len(t))
    return t[:p] + tag + sent(r, 2)[:-1] + t[p:] + "。"


def gen(seed, kmin=41, kmax=130):
    """与审查员 p10-bigfuzz.py 相同的随机序列：返回 (kind, style, K, base, mine, theirs, tmod, tdel) 或 None（文字撞车）。"""
    r = random.Random(seed)
    style = r.choice(["rand", "templ", "mixed"])
    kind = r.choice(["chapter", "shiftpaste"])
    n = r.randint(60, 160)

    def btext(i):
        if style == "templ" or (style == "mixed" and r.random() < 0.5):
            return f"第{i + 1}段：原始内容 {i + 1}。"
        return sent(r)

    base = [(i, btext(i)) for i in range(n)]
    k = r.randint(kmin, min(kmax, n - 4))
    s = r.randint(1, n - k - 1)
    region = base[s:s + k]
    if kind == "chapter":
        reg = [(bid, small_edit(r, t, "〔我〕")) for bid, t in region]
        d = r.randint(1, 4)
        for _ in range(d):
            reg.pop(r.randrange(len(reg)))
        for _ in range(d):
            reg.insert(r.randrange(len(reg) + 1), (f"n{r.random()}", f"第{r.randint(200, 999)}段：原始内容 {r.randint(200, 999)}。" if style != "rand" else sent(r)))
    else:
        reg = list(region)
        moved = reg.pop(r.randrange(len(reg)))
        reg.insert(r.randrange(len(reg) + 1), moved)
        reg = [(bid, small_edit(r, t, "〔我〕")) for bid, t in reg]
    mine = base[:s] + reg + base[s + k:]
    theirs, tmod, tdel = [], set(), set()
    for bid, t in base:
        x = r.random()
        if x < 0.03:
            tdel.add(bid)
            continue
        if x < 0.13:
            tmod.add(bid)
            theirs.append((bid, small_edit(r, t, "〔张〕")))
            continue
        theirs.append((bid, t))
    return kind, style, k, base, mine, theirs, tmod, tdel


def problems_of(base, mine, theirs, tmod, tdel):
    """按身份检查合并结果（审查员同一套判据）：重复段、丢段、丢改、双改无冲突覆盖等。"""
    owner = {}
    for lst in (base, mine, theirs):
        for bid, t in lst:
            if owner.setdefault(t, bid) != bid:
                return None
    res = merge3("\n\n".join(t for _, t in base), "\n\n".join(t for _, t in theirs), "\n\n".join(t for _, t in mine))
    out = [b for b in res.markdown.split("\n\n") if b]
    inconf = set()
    for c in res.conflicts:
        for side in (c.base, c.mine, c.theirs):
            for b in side:
                if b.body in owner:
                    inconf.add(owner[b.body])
    cnt = collections.Counter(owner.get(b, "?") for b in out)
    mmap, tmap, bmap = dict(mine), dict(theirs), dict(base)
    found = set()
    if any(v > 1 for key, v in cnt.items() if key != "?"):
        found.add("重复段")
    for bid in set(bmap) | set(mmap):
        mdel = bid in bmap and bid not in mmap
        mmod = bid in mmap and bid in bmap and mmap[bid] != bmap[bid]
        if bid in inconf:
            continue
        if bid not in bmap:
            if cnt[bid] == 0:
                found.add("丢我的新段")
            continue
        if mdel and bid in tmod:
            found.add("对方改的段被我删且无冲突")
            continue
        if bid in tdel and mmod:
            found.add("我改的段被对方删且无冲突")
            continue
        if mmod and bid in tmod:
            if tmap[bid] not in out:
                found.add("覆盖对方(双改无冲突)")
            continue
        present = not mdel and bid not in tdel
        if present and cnt[bid] == 0:
            found.add("丢段")
        elif present:
            want = mmap[bid] if mmod else (tmap[bid] if bid in tmod else bmap[bid])
            if want not in out:
                found.add("丢改")
    return found


def test_reviewer_seed7_and_seed0_have_no_duplicate_paragraphs():
    """审查员复现一：SEED=7（模板化整章修改，4 个条目全错配、正文 3 处重复段）、SEED=0（1 处）。"""
    for seed in (7, 0):
        kind, style, k, base, mine, theirs, tmod, tdel = gen(seed)
        assert k > 40
        assert problems_of(base, mine, theirs, tmod, tdel) == set(), (seed, kind, style, k)


def test_45_templated_blocks_chapter_edit_no_duplicates():
    """45 块模板化段落整章小改（区间里删 1 段、插 1 段），对方改了其中几段：正文里不能有同一段的两个版本。"""
    base = [(i, f"第{i + 1}段：原始内容 {i + 1}。") for i in range(110)]
    region = [(bid, t.replace("：", f"〔我{bid}〕：", 1)) for bid, t in base[5:50]]
    region.pop(20)
    region.insert(30, ("new", "第300段：原始内容 300。"))
    mine = base[:5] + region + base[50:]
    tmod = {7, 9, 21, 33}
    theirs = [(bid, t.replace("内容", f"内〔张{bid}〕容") if bid in tmod else t) for bid, t in base]
    assert problems_of(base, mine, theirs, tmod, set()) == set()


def test_region_over_limit_is_one_whole_conflict():
    """> PAIR_EXACT_LIMIT 块的等长替换区间：不再按位置逐对拆（旧规则已删除），整块作为一个结构冲突。"""
    size = M.PAIR_EXACT_LIMIT + 5
    base = [f"第{i + 1}段：原始内容 {i + 1}。" for i in range(size + 10)]
    mine = base[:5] + [t.replace("内容", "内容（我改）") for t in base[5:5 + size]] + base[5 + size:]
    theirs = list(base)
    theirs[8] = theirs[8].replace("内容", "内容（张改）")
    res = merge3("\n\n".join(base), "\n\n".join(theirs), "\n\n".join(mine))
    assert len(res.conflicts) == 1
    out = res.markdown.split("\n\n")
    assert len(out) == len(set(out)) == len(base)
    assert not hasattr(M, "_better")


def test_big_region_fuzz_small_scale_has_no_duplicates():
    """审查员 fuzz 的小规模版：41–90 块的整章修改 / 挪动粘贴，各种文本风格；重复段与其他身份问题都必须为 0。"""
    stats = collections.Counter()
    started = time.perf_counter()
    for seed in range(120):
        _, _, _, base, mine, theirs, tmod, tdel = gen(seed, 41, 90)
        found = problems_of(base, mine, theirs, tmod, tdel)
        if found is None:
            continue
        stats["runs"] += 1
        for item in found:
            stats[item] += 1
    assert stats["runs"] >= 100
    assert stats == collections.Counter(runs=stats["runs"]), stats
    assert time.perf_counter() - started < 60
