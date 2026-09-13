#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dota2 官方元数据：版本链 + 英雄/装备官方中文名。

数据源全部是 Valve 官方 datafeed（免 key）：
  - 版本列表（含小版本与发布时间）: /datafeed/patchnoteslist
  - 英雄中文名                    : /datafeed/herolist?language=schinese
  - 装备中文名                    : /datafeed/itemlist?language=schinese
  - 某版本改动明细                : /datafeed/patchnotes?version=X

本地缓存到 ~/.cache/dota2-hero-build/，默认 24h 过期（--refresh 强制刷新）。

用法:
    python3 dota_meta.py --patches               # 最近版本与日期
    python3 dota_meta.py --chain 7.41e           # 小版本回退链（不跨大版本）
    python3 dota_meta.py --hero 虚空假面          # 解析英雄（含冲突英雄提示）
    python3 dota_meta.py --hero-changes 虚空假面 --patch 7.41e
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

BASE = "https://www.dota2.com/datafeed"
CACHE_DIR = os.path.expanduser("~/.cache/dota2-hero-build")
CACHE_TTL = 24 * 3600
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url, cache_key, ttl=CACHE_TTL, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, cache_key + ".json")
    if not refresh and os.path.exists(path):
        if (dt.datetime.now().timestamp() - os.path.getmtime(path)) < ttl:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


# ------------------------------------------------------------------ 版本

def patches(refresh=False):
    """[{name, date(datetime.date), ts}]，按时间升序。"""
    d = _get(BASE + "/patchnoteslist?language=english", "patchlist", refresh=refresh)
    out = []
    for p in (d.get("patches") or []):
        ts = p.get("patch_timestamp") or p.get("timestamp")
        name = p.get("patch_number") or p.get("patch_name")
        if not (ts and name):
            continue
        out.append({"name": str(name), "ts": int(ts),
                    "date": dt.datetime.fromtimestamp(int(ts)).date()})
    out.sort(key=lambda x: x["ts"])
    return out


def major_of(name):
    """'7.41e' -> '7.41'；'7.41' -> '7.41'"""
    m = re.match(r"^(\d+\.\d+)", str(name))
    return m.group(1) if m else str(name)


def current_patch(refresh=False):
    ps = patches(refresh)
    return ps[-1] if ps else None


def find_patch(name, refresh=False):
    for p in patches(refresh):
        if p["name"] == name:
            return p
    return None


def patch_window(name, refresh=False):
    """某版本的生效窗口 [start_date, end_date)。end 为 None 表示当前版本，延续到现在。"""
    ps = patches(refresh)
    for i, p in enumerate(ps):
        if p["name"] == name:
            nxt = ps[i + 1] if i + 1 < len(ps) else None
            return p["date"], (nxt["date"] if nxt else None)
    return None, None


def fallback_chain(name, refresh=False):
    """从 name 往前回退的小版本链，**只在同一个大版本内**，含 name 本身。

    7.41e -> [7.41e, 7.41d, 7.41c, 7.41b, 7.41a, 7.41]
    到大版本（7.41）为止，绝不跨到 7.40。
    """
    ps = patches(refresh)
    mj = major_of(name)
    same = [p for p in ps if major_of(p["name"]) == mj]
    same.sort(key=lambda x: x["ts"])
    idx = next((i for i, p in enumerate(same) if p["name"] == name), None)
    if idx is None:
        return []
    return list(reversed(same[: idx + 1]))


# --------------------------------------------------------------- 英雄/装备

def heroes(refresh=False):
    d = _get(BASE + "/herolist?language=schinese", "herolist_cn", refresh=refresh)
    return d.get("result", {}).get("data", {}).get("heroes", []) or []


def items(refresh=False):
    d = _get(BASE + "/itemlist?language=schinese", "itemlist_cn", refresh=refresh)
    return d.get("result", {}).get("data", {}).get("itemabilities", []) or []



def _slang_aliases():
    """读同目录 slang.json 的 hero_aliases（可选，缺失则返回 {}）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slang.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {k: v for k, v in (d.get("hero_aliases") or {}).items()
                if not k.startswith("_") and isinstance(v, list)}
    except Exception:
        return {}

def resolve_hero(query, refresh=False):
    """按官方中文名 / 英文名 / npc 名解析英雄。**精确优先，绝不模糊猜。**

    返回 {hero, exact(bool), collisions:[...]}；collisions 是中文名互相包含的其它英雄
    （例如 虚空假面 vs 虚无之灵——玩家口语会把后者也叫『虚空之灵/小虚空』）。
    """
    q = (query or "").strip()
    hs = heroes(refresh)
    exact = [h for h in hs
             if q == h.get("name_loc") or q.lower() == (h.get("name_english_loc") or "").lower()
             or q == h.get("name")]
    if not exact:
        # 再查 slang.json 里人工确认过的俗称（如 隐刺 -> 力丸）。
        # 仍然是**精确**匹配别名，不做模糊猜测。
        for cn, al in (_slang_aliases() or {}).items():
            if any(q == a or q.lower() == str(a).lower() for a in al):
                exact = [h for h in hs if h.get("name_loc") == cn]
                break
    hero = exact[0] if exact else None
    if not hero:
        part = [h for h in hs if q and (q in h.get("name_loc", "")
                                        or q.lower() in (h.get("name_english_loc") or "").lower())]
        return {"hero": None, "exact": False, "candidates": part, "collisions": []}

    cn = hero["name_loc"]
    # 冲突：中文名有公共字且不是自己（用于生成排除词）
    coll = []
    for h in hs:
        if h["id"] == hero["id"]:
            continue
        common = set(cn) & set(h["name_loc"])
        if len(common) >= 2 or (len(cn) >= 2 and cn[:2] in h["name_loc"]):
            coll.append(h)
    return {"hero": hero, "exact": True, "candidates": [], "collisions": coll}


def hero_changes(hero_id, patch_name, refresh=False):
    d = _get(BASE + "/patchnotes?version=%s&language=english" % patch_name,
             "patchnotes_" + patch_name, refresh=refresh)
    hit = [h for h in (d.get("heroes") or []) if h.get("hero_id") == hero_id]
    return {"hero": hit, "items": d.get("items") or [],
            "hero_change_count": len(d.get("heroes") or [])}


# ------------------------------------------------------------------ CLI

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--patches", action="store_true")
    p.add_argument("--chain", metavar="VERSION")
    p.add_argument("--hero", metavar="NAME")
    p.add_argument("--hero-changes", metavar="NAME")
    p.add_argument("--patch", metavar="VERSION")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    R = a.refresh

    if a.patches:
        ps = patches(R)
        cur = ps[-1]
        print("当前版本：%s（%s）" % (cur["name"], cur["date"]))
        print("最近 12 个版本：")
        for x in ps[-12:]:
            print("  %-8s %s%s" % (x["name"], x["date"], "   ← 当前" if x is cur else ""))
        return 0

    if a.chain:
        ch = fallback_chain(a.chain, R)
        if not ch:
            print("找不到版本 %s" % a.chain); return 1
        print("小版本回退链（大版本 %s 内，不跨版本）：" % major_of(a.chain))
        for x in ch:
            s, e = patch_window(x["name"], R)
            print("  %-8s %s ~ %s" % (x["name"], s, e or "至今"))
        if a.json:
            print(json.dumps([{"name": x["name"], "date": str(x["date"])} for x in ch],
                             ensure_ascii=False))
        return 0

    if a.hero:
        r = resolve_hero(a.hero, R)
        if not r["hero"]:
            print("❌ 没有精确匹配 %r。" % a.hero)
            if r["candidates"]:
                print("   相近的英雄（请用完整官方名重试，本工具不做模糊猜测）：")
                for h in r["candidates"][:10]:
                    print("     %s | %s" % (h["name_loc"], h["name_english_loc"]))
            return 1
        h = r["hero"]
        print("✅ %s | %s | id=%s | %s" % (h["name_loc"], h["name_english_loc"],
                                           h["id"], h["name"]))
        if r["collisions"]:
            print("⚠️  易混英雄（搜索时需排除，玩家口语常混用）：")
            for c in r["collisions"]:
                print("     %s | %s" % (c["name_loc"], c["name_english_loc"]))
        if a.json:
            print(json.dumps({"id": h["id"], "cn": h["name_loc"],
                              "en": h["name_english_loc"],
                              "collisions": [c["name_loc"] for c in r["collisions"]]},
                             ensure_ascii=False))
        return 0

    if a.hero_changes:
        r = resolve_hero(a.hero_changes, R)
        if not r["hero"]:
            print("❌ 英雄名无法精确解析"); return 1
        pv = a.patch or current_patch(R)["name"]
        ch = hero_changes(r["hero"]["id"], pv, R)
        print("%s @ %s：%s" % (r["hero"]["name_loc"], pv,
                              "有改动" if ch["hero"] else "**本版本无改动**"))
        if ch["hero"]:
            print(json.dumps(ch["hero"], ensure_ascii=False, indent=2)[:2000])
        print("（本版本共改了 %d 个英雄、%d 件装备）"
              % (ch["hero_change_count"], len(ch["items"])))
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
