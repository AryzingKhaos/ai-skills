#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dota2-match-review · 列出近期对局，用来把"最近的那局幽鬼""倒数第二近的XX"解析成 match_id。
用法:
    python3 list_matches.py [--account 137084212] [--limit 40] [--hero Spectre]
- 不带 --hero: 按时间倒序列出最近 N 局(所有英雄), 带"第N近"序号。
- 带 --hero <英文名子串>: 只列该英雄的对局, 按时间倒序并标"第N近", 直接回答"倒数第N近的某英雄"。
  （--hero 由 Claude 内部用——用户只说中文英雄名, 由 Claude 映射成英文再传, 用户不必记参数。）
仅标准库, 需联网。
"""
import sys, json, argparse, datetime, urllib.request

API = "https://api.opendota.com/api"
DEFAULT_ACCOUNT = 137084212
LOBBY = {0:"普通",1:"练习",2:"赛事",4:"教程",5:"组排",6:"单中",7:"天梯",9:"勇士联赛"}

def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "dota2-match-review/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def is_win(m): return (m["player_slot"] < 128) == m["radiant_win"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=DEFAULT_ACCOUNT)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--hero", default=None, help="英文英雄名子串(如 Spectre/Chaos)，只列该英雄")
    a = ap.parse_args()

    heroes = get(f"{API}/heroes")
    LOC = {h["id"]: h["localized_name"] for h in heroes}
    rec = get(f"{API}/players/{a.account}/matches?limit={a.limit}")
    rec.sort(key=lambda m: m.get("start_time", 0), reverse=True)  # 最近在前

    if a.hero:
        key = a.hero.strip().lower()
        rec = [m for m in rec if key in LOC.get(m["hero_id"], "").lower()]
        if not rec:
            print(f"近 {a.limit} 局里没有英雄名含 “{a.hero}” 的对局。换个英雄名或加大 --limit。")
            return
        print(f"账号 {a.account} · 英雄含“{a.hero}” · 共 {len(rec)} 局（最近在前）:")
        for i, m in enumerate(rec, 1):
            d = datetime.date.fromtimestamp(m["start_time"]).isoformat()
            print(f"  第{i}近  match_id={m['match_id']}  {d}  {LOBBY.get(m.get('lobby_type'),m.get('lobby_type'))}  "
                  f"{LOC.get(m['hero_id'],'?')}  {'胜' if is_win(m) else '负'}  {m['duration']//60}分  "
                  f"K/D/A={m['kills']}/{m['deaths']}/{m['assists']}")
        print('\n提示："最近的那局"=第1近, "倒数第二近的"=第2近, 以此类推。')
        return

    print(f"账号 {a.account} · 最近 {len(rec)} 局（最近在前）:")
    for i, m in enumerate(rec, 1):
        d = datetime.date.fromtimestamp(m["start_time"]).isoformat()
        print(f"  第{i}近  match_id={m['match_id']}  {d}  {LOBBY.get(m.get('lobby_type'),m.get('lobby_type')):<5} "
              f"{LOC.get(m['hero_id'],'?'):<18}{'胜' if is_win(m) else '负'}  {m['duration']//60}分  "
              f"K/D/A={m['kills']}/{m['deaths']}/{m['assists']}")

if __name__ == "__main__":
    main()
