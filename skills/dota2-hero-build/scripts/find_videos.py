#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸门一：搜 B 站 + 纯元数据筛选，产出候选清单。**零下载成本。**

流程：
  1. 用 dota_meta 精确解析英雄（拒绝模糊匹配）→ 官方中文名 + 别名 + 冲突词
  2. 按小版本窗口 [版本发布日, 下一版本发布日) 过滤发布时间
  3. 元数据闸门（全部零成本，命中即淘汰）：
       - 其它游戏关键词 / 标签
       - DotA1 标记、UP 主黑名单
       - 英雄冲突（提到易混英雄而本英雄缺席）
       - 时长下限
  4. 教学意图打分排序；集锦/切片类降权
  5. 数量不足 --want 时，沿 dota_meta.fallback_chain 往前一个小版本继续找，
     **绝不跨大版本**

用法:
    python3 find_videos.py --hero 虚空假面 --want 3 --out cand.json
    python3 find_videos.py --hero 虚空假面 --patch 7.41d --no-fallback
"""

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dota_meta  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
COOKIE_FILE = os.path.expanduser("~/.cache/dota2-hero-build/bili_cookies.txt")


def load_slang():
    with open(os.path.join(HERE, "slang.json"), encoding="utf-8") as f:
        return json.load(f)


def ensure_cookies(browser="chrome"):
    """B 站搜索 API 需要登录 cookie，否则 412。用 yt-dlp 导出一次，缓存复用。"""
    if os.path.exists(COOKIE_FILE) and \
       (time.time() - os.path.getmtime(COOKIE_FILE)) < 6 * 3600:
        return COOKIE_FILE
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    cmd = ('yt-dlp --cookies-from-browser %s --cookies "%s" --skip-download '
           '--playlist-items 0 "https://www.bilibili.com/video/BV1xx411c7mD" '
           '>/dev/null 2>&1' % (browser, COOKIE_FILE))
    os.system(cmd)
    if not os.path.exists(COOKIE_FILE):
        raise SystemExit("导不出 B 站 cookie。请先在 %s 里登录 bilibili.com。" % browser)
    return COOKIE_FILE


def opener():
    cj = http.cookiejar.MozillaCookieJar(ensure_cookies())
    cj.load(ignore_discard=True, ignore_expires=True)
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA), ("Referer", "https://www.bilibili.com/")]
    return op


def clean(s):
    return re.sub(r"</?em[^>]*>", "", urllib.parse.unquote(str(s or "")))


def search(op, keyword, order="click", page=1):
    url = ("https://api.bilibili.com/x/web-interface/search/type?search_type=video"
           "&keyword=" + urllib.parse.quote(keyword) +
           "&order=%s&page=%d" % (order, page))
    try:
        d = json.loads(op.open(url, timeout=30).read().decode("utf-8"))
    except Exception as e:
        print("  [搜索失败] %s (%s): %s" % (keyword, order, e), file=sys.stderr)
        return []
    return (d.get("data", {}) or {}).get("result") or []


def dur_seconds(text):
    parts = str(text or "").split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return 0


def blob_of(v):
    return " ".join([clean(v.get("title")), str(v.get("tag") or ""),
                     str(v.get("author") or ""), clean(v.get("description"))]).lower()


def gate(v, hero_cn, aliases, conflicts, slang, min_seconds, allow_modes=False):
    """返回 (通过?, 淘汰原因, 加分项列表)。全部基于元数据，零下载。"""
    blob = blob_of(v)
    title = clean(v.get("title"))
    author = (v.get("author") or "").strip()

    for kw in slang["other_game_markers"]["keywords"]:
        if kw.lower() in blob:
            return False, "其它游戏：%s" % kw, []

    for kw in slang["dota1_markers"]["keywords"]:
        if kw.lower() in blob:
            return False, "DotA1 标记：%s" % kw, []

    if not allow_modes:
        for kw in slang.get("game_mode_markers", {}).get("keywords", []):
            if kw.lower() in blob:
                return False, "非常规模式：%s（出装不可迁移到天梯）" % kw, []

    bl = {k: v2 for k, v2 in slang["uploader_blacklist"].items() if not k.startswith("_")}
    if author in bl:
        return False, "UP主黑名单：%s（%s）" % (author, bl[author]), []

    # 正向闸门：必须有 Dota 语境。别名往往很泛（「虚空」能匹配星露谷/群星/公开课），
    # 只靠"命中别名"会把大量无关内容放进来。
    if not any(k in blob for k in slang["dota_context_markers"]["keywords"]):
        return False, "无Dota语境：标题/标签无 dota/刀塔 等标记", []

    names = [hero_cn] + list(aliases)
    if not any(n.lower() in blob for n in names if n):
        return False, "英雄缺席：标题/标签未出现该英雄", []

    # 冲突英雄：提到易混者，且本英雄官方全名没出现 → 多半讲的是另一个英雄
    for c in conflicts:
        if c.lower() in blob and hero_cn.lower() not in blob:
            return False, "疑似易混英雄：%s" % c, []

    secs = dur_seconds(v.get("duration"))
    if secs < min_seconds:
        return False, "时长不足：%ds < %ds" % (secs, min_seconds), []

    bonus = []
    wl = {k: v2 for k, v2 in slang["uploader_whitelist"].items() if not k.startswith("_")}
    if author in wl:
        bonus.append("UP主白名单")
    return True, None, bonus


def score(v, slang, bonus):
    title = clean(v.get("title"))
    s = 0.0
    for kw in slang["teach_intent_keywords"]["strong"]:
        if kw in title:
            s += 3
    for kw in slang["teach_intent_keywords"]["weak"]:
        if kw in title:
            s += 1
    for kw in slang["lowvalue_markers"]["keywords"]:
        if kw in title:
            s -= 2          # 集锦/切片：画面可能是真的，但转写稿基本是空的
    if "UP主白名单" in bonus:
        s += 5
    secs = dur_seconds(v.get("duration"))
    if secs >= 240:
        s += 1
    if secs >= 600:
        s += 1
    s += min((v.get("play") or 0) / 50000.0, 1.0)   # 播放量只做微弱加权
    return round(s, 2)


def collect_for_patch(op, patch, hero_cn, aliases, conflicts, slang,
                      min_seconds, pages, sleep, allow_modes=False):
    start, end = dota_meta.patch_window(patch["name"])
    lo = int(dt.datetime.combine(start, dt.time.min).timestamp())
    hi = int(dt.datetime.combine(end, dt.time.min).timestamp()) if end else None

    kws = []
    for n in [hero_cn] + list(aliases):
        kws += ["%s 出装" % n, "%s 教学" % n, "%s 攻略" % n, "dota2 %s" % n]
    seen, raw = {}, 0
    for kw in kws:
        for order in ("click", "pubdate"):
            for page in range(1, pages + 1):
                for v in search(op, kw, order, page):
                    raw += 1
                    b = v.get("bvid")
                    if b and b not in seen:
                        seen[b] = v
                time.sleep(sleep)

    inwin, passed, rejects = [], [], []
    for v in seen.values():
        pd = v.get("pubdate") or 0
        if pd < lo or (hi and pd >= hi):
            continue
        inwin.append(v)
        ok, why, bonus = gate(v, hero_cn, aliases, conflicts, slang, min_seconds, allow_modes)
        if ok:
            passed.append({
                "bvid": v["bvid"], "title": clean(v.get("title")),
                "author": v.get("author"), "play": v.get("play"),
                "duration": v.get("duration"), "duration_s": dur_seconds(v.get("duration")),
                "pubdate": dt.datetime.fromtimestamp(pd).strftime("%Y-%m-%d"),
                "tag": v.get("tag"), "patch": patch["name"],
                "score": score(v, slang, bonus), "bonus": bonus,
                "url": "https://www.bilibili.com/video/%s" % v["bvid"],
            })
        else:
            rejects.append({"bvid": v.get("bvid"), "title": clean(v.get("title")),
                            "author": v.get("author"), "reason": why})
    passed.sort(key=lambda x: -x["score"])
    return {"patch": patch["name"],
            "window": [str(start), str(end) if end else "至今"],
            "raw_hits": raw, "deduped": len(seen), "in_window": len(inwin),
            "passed": passed, "rejects": rejects}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hero", required=True, help="英雄官方中文名（精确匹配）")
    p.add_argument("--patch", help="起始版本，默认当前版本")
    p.add_argument("--want", type=int, default=3, help="目标候选数")
    p.add_argument("--min-seconds", type=int, default=180, help="时长下限")
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--sleep", type=float, default=0.6)
    p.add_argument("--no-fallback", action="store_true", help="不回退到更早小版本")
    p.add_argument("--allow-modes", action="store_true",
                   help="放行加速模式/OMG/大乱斗等非常规模式（默认淘汰）")
    p.add_argument("--out", help="候选清单 JSON 输出路径")
    a = p.parse_args()

    slang = load_slang()
    r = dota_meta.resolve_hero(a.hero)
    if not r["hero"]:
        print("❌ 英雄名无法精确解析：%r" % a.hero)
        for h in r["candidates"][:10]:
            print("   相近：%s | %s" % (h["name_loc"], h["name_english_loc"]))
        print("   本工具拒绝模糊匹配，请用完整官方中文名。")
        return 1
    hero_cn = r["hero"]["name_loc"]
    aliases = [x for x in slang["hero_aliases"].get(hero_cn, []) if not str(x).startswith("_")]
    conflicts = [x for x in slang["hero_conflicts"].get(hero_cn, []) if not str(x).startswith("_")]

    start_patch = a.patch or dota_meta.current_patch()["name"]
    chain = dota_meta.fallback_chain(start_patch)
    if not chain:
        print("❌ 版本 %s 不存在" % start_patch)
        return 1
    if a.no_fallback:
        chain = chain[:1]

    print("英雄：%s | %s (id=%s)" % (hero_cn, r["hero"]["name_english_loc"], r["hero"]["id"]))
    print("别名：%s" % (aliases or "—"))
    print("冲突词：%s" % (conflicts or "—"))
    print("版本链（大版本 %s 内）：%s"
          % (dota_meta.major_of(start_patch), " → ".join(x["name"] for x in chain)))
    print()

    op = opener()
    rounds, chosen = [], []
    for pt in chain:
        res = collect_for_patch(op, pt, hero_cn, aliases, conflicts, slang,
                                a.min_seconds, a.pages, a.sleep, a.allow_modes)
        rounds.append(res)
        have = {c["bvid"] for c in chosen}
        for c in res["passed"]:
            if c["bvid"] not in have:
                chosen.append(c)
                have.add(c["bvid"])
        print("[%s] %s ~ %s | 去重 %d，窗口内 %d，过闸 %d，累计候选 %d"
              % (res["patch"], res["window"][0], res["window"][1],
                 res["deduped"], res["in_window"], len(res["passed"]), len(chosen)))
        if len(chosen) >= a.want:
            break
    else:
        if len(chosen) < a.want:
            print("\n⚠️ 回退到大版本 %s 起点仍只找到 %d 个候选（目标 %d）。"
                  "按规则不跨大版本，就用这些。"
                  % (dota_meta.major_of(start_patch), len(chosen), a.want))

    chosen.sort(key=lambda x: -x["score"])
    print("\n=== 候选（已过元数据闸门，待画面核验）===")
    if not chosen:
        print("  无。")
    for i, c in enumerate(chosen[: max(a.want * 3, 9)], 1):
        print("%2d. [%s] %-13s 分%-5s %7s播 %6s %s"
              % (i, c["patch"], c["bvid"], c["score"], c["play"], c["duration"],
                 c["title"][:38]))
        print("      UP: %s%s" % (c["author"], "  ⭐白名单" if c["bonus"] else ""))

    rej = [x for r_ in rounds for x in r_["rejects"]]
    if rej:
        from collections import Counter
        cnt = Counter(x["reason"].split("：")[0] for x in rej)
        print("\n=== 元数据闸门淘汰 %d 个（零下载成本）===" % len(rej))
        for k, n in cnt.most_common():
            print("  %-18s %d" % (k, n))

    out = {"hero": {"cn": hero_cn, "en": r["hero"]["name_english_loc"], "id": r["hero"]["id"]},
           "aliases": aliases, "conflicts": conflicts,
           "start_patch": start_patch, "want": a.want,
           "chain_used": [x["patch"] for x in rounds],
           "candidates": chosen, "rounds": rounds}
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("\n候选清单 → %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
