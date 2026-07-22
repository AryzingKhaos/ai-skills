#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dota2-match-review · 英雄相性取数脚本（队友协同 + 对手克制）
用法:
    python3 fetch_matchups.py <match_id> [--account 137084212] [--append /path/_review_<id>.md] [--out /path/snip.md] [--token-file PATH]

做的事（全部确定性，只取"你这局所用英雄"与在场其它英雄的历史胜负相性）:
    1) 从 OpenDota 拿该局阵容，定位【你的英雄】+ 4 个队友英雄 + 5 个对手英雄（不需要解析，秒回）
    2) 相性数据源（优先 STRATZ，回退 OpenDota）:
       - STRATZ heroStats.matchUp(heroId=你的英雄): with(同队协同) + vs(对位克制)，含 winCount/matchCount/synergy
       - 无 token / STRATZ 失败 → OpenDota /heroes/{id}/matchups 仅能给"对手克制"(vs)胜率，无协同
    3) 打印 "## 11. 英雄相性（队友协同 + 对手克制）" 数据卡 + 一段 matchups 信号 JSON

判断（这局阵容顺不顺、被谁克、和谁配）交给 SKILL.md 的 Claude；本脚本只给相性数字。
synergy 是 STRATZ 归一化后的"协同/克制强度"（正=比期望更强），winrate 是原始样本胜率。
仅用标准库(urllib)。需要联网。失败静默降级（不报错中断、不编数）。
"""
import os, sys, json, argparse, urllib.request, urllib.error

OD = "https://api.opendota.com/api"
STRATZ = "https://api.stratz.com/graphql"
DEFAULT_ACCOUNT = 137084212

def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "dota2-match-review/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def load_token(token_file):
    t = os.environ.get("STRATZ_TOKEN")
    if t and t.strip():
        return t.strip()
    for path in [token_file, os.path.expanduser("~/.stratz_token")]:
        if path and os.path.exists(path):
            try:
                v = open(path, encoding="utf-8").read().strip()
                if v:
                    return v
            except Exception:
                pass
    return None

def stratz_matchup(token, hero_id):
    """返回 {'with': {hid2:(wc,mc,syn)}, 'vs': {hid2:(wc,mc,syn)}}，失败返回 None。"""
    q = ("query($h:Short!){heroStats{matchUp(heroId:$h,take:300){"
         "with{heroId2 winCount matchCount synergy} vs{heroId2 winCount matchCount synergy}}}}")
    body = json.dumps({"query": q, "variables": {"h": hero_id}}).encode("utf-8")
    req = urllib.request.Request(STRATZ, data=body, method="POST", headers={
        "Authorization": "Bearer " + token, "User-Agent": "STRATZ_API", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode("utf-8"))
    mu = (((resp.get("data") or {}).get("heroStats") or {}).get("matchUp") or [])
    if not mu:
        return None
    row = mu[0]
    def pack(arr):
        return {e["heroId2"]: (e.get("winCount") or 0, e.get("matchCount") or 0, e.get("synergy"))
                for e in (arr or [])}
    return {"with": pack(row.get("with")), "vs": pack(row.get("vs"))}

def wr(wc, mc):
    return (100.0 * wc / mc) if mc else None

def wr_tag(w):
    if w is None: return ""
    if w >= 54: return "✅强"
    if w >= 51: return "偏好"
    if w > 49:  return "中性"
    if w > 46:  return "偏差"
    return "⚠️弱"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id", type=int)
    ap.add_argument("--account", type=int, default=DEFAULT_ACCOUNT)
    ap.add_argument("--out", default=None)
    ap.add_argument("--append", default=None)
    ap.add_argument("--token-file", default=None)
    a = ap.parse_args()

    out = []
    P = out.append
    P("## 11. 英雄相性（你这局英雄 × 队友协同 / 对手克制）")

    def emit(note=None, signals=None):
        if note:
            P(note)
        P("")
        P("### 相性信号 JSON（供判断用，勿直接展示给用户）")
        P("```json")
        P(json.dumps(signals or {"source": "matchups", "parsed": False}, ensure_ascii=False, indent=2))
        P("```")
        text = "\n".join(out)
        print(text)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        if a.append and os.path.exists(a.append):
            with open(a.append, "a", encoding="utf-8") as f:
                f.write("\n" + text + "\n")
            print("\n[已把英雄相性片段追加到 " + a.append + "]", file=sys.stderr)

    # --- 阵容（不触发解析，直接拿 hero_id） ---
    try:
        heroes = get(f"{OD}/heroes")
        LOC = {h["id"]: h["localized_name"] for h in heroes}
        m = get(f"{OD}/matches/{a.match_id}")
    except Exception as e:
        emit("> ⚠️ 拉取阵容失败：" + str(e) + "。跳过英雄相性。",
             {"source": "matchups", "parsed": False, "reason": "fetch_failed"})
        return
    players = m.get("players") or []
    me = next((p for p in players if p.get("account_id") == a.account), None)
    if me is None:
        emit("> ⚠️ 账号不在该局，跳过英雄相性。",
             {"source": "matchups", "parsed": False, "reason": "account_not_in_match"})
        return
    my_hero = me["hero_id"]
    rad = me["player_slot"] < 128
    mates = [p for p in players if (p["player_slot"] < 128) == rad and p is not me]
    opps = [p for p in players if (p["player_slot"] < 128) != rad]

    # --- 相性数据源：STRATZ 优先，OpenDota 回退 ---
    token = load_token(a.token_file)
    data = None; src = None
    if token:
        try:
            data = stratz_matchup(token, my_hero)
            if data:
                src = "stratz"
        except Exception:
            data = None
    od_vs = None
    if data is None:  # 回退：OpenDota 只有 vs（对手克制），无 with（协同）
        try:
            raw = get(f"{OD}/heroes/{my_hero}/matchups")
            od_vs = {x["hero_id"]: (x["wins"], x["games_played"]) for x in raw}
            src = "opendota"
        except Exception:
            od_vs = None
    if data is None and od_vs is None:
        emit("> ⚠️ STRATZ 与 OpenDota 相性都拉取失败，跳过英雄相性。",
             {"source": "matchups", "parsed": False, "reason": "both_failed"})
        return

    sig = {"source": src, "parsed": True, "my_hero": LOC.get(my_hero), "with": [], "vs": []}

    # ---------- 队友协同 with ----------
    P("- **你的英雄：%s**（相性源：%s%s）" % (
        LOC.get(my_hero), src, "（含协同+克制）" if src == "stratz" else "（仅对手克制，协同需 STRATZ token）"))
    if src == "stratz":
        P("")
        P("### 11a. 与队友的协同（同队历史胜率 / synergy，越高越搭）")
        with_rows = []
        for p in sorted(mates, key=lambda x: x["hero_id"]):
            wc, mc, syn = data["with"].get(p["hero_id"], (0, 0, None))
            w = wr(wc, mc)
            with_rows.append((p, w, syn, wc, mc))
        for p, w, syn, wc, mc in sorted(with_rows, key=lambda r: (r[1] if r[1] is not None else -1), reverse=True):
            P("- 与 **%s**：胜率 %s（%d/%d）· synergy %s %s" % (
                LOC.get(p["hero_id"]), ("%.1f%%" % w) if w is not None else "无样本", wc, mc,
                ("%+.2f" % syn) if syn is not None else "—", wr_tag(w)))
            sig["with"].append({"hero": LOC.get(p["hero_id"]), "winrate": round(w, 1) if w is not None else None,
                                "synergy": syn, "games": mc})

    # ---------- 对手克制 vs ----------
    P("")
    P("### 11b. 对上对手的克制关系（你的英雄 vs 各对手历史胜率）")
    vs_rows = []
    for p in sorted(opps, key=lambda x: x["hero_id"]):
        if src == "stratz":
            wc, mc, syn = data["vs"].get(p["hero_id"], (0, 0, None))
        else:
            wc, mc = od_vs.get(p["hero_id"], (0, 0)); syn = None
        w = wr(wc, mc)
        vs_rows.append((p, w, syn, wc, mc))
    for p, w, syn, wc, mc in sorted(vs_rows, key=lambda r: (r[1] if r[1] is not None else -1), reverse=True):
        verdict = "你克他" if (w is not None and w >= 52) else ("他克你" if (w is not None and w <= 48) else "五五")
        P("- vs **%s**：胜率 %s（%d/%d）%s → **%s** %s" % (
            LOC.get(p["hero_id"]), ("%.1f%%" % w) if w is not None else "无样本", wc, mc,
            ("· synergy %+.2f" % syn) if syn is not None else "", verdict, wr_tag(w)))
        sig["vs"].append({"hero": LOC.get(p["hero_id"]), "winrate": round(w, 1) if w is not None else None,
                          "synergy": syn, "games": mc, "verdict": verdict})

    # ---------- 概览 ----------
    good_vs = [r for r in sig["vs"] if r["winrate"] is not None and r["winrate"] >= 52]
    bad_vs = [r for r in sig["vs"] if r["winrate"] is not None and r["winrate"] <= 48]
    P("")
    P("- 概览：对手里你**天然占优** %d 个%s；**被克** %d 个%s。" % (
        len(good_vs), ("（" + "、".join(r["hero"] for r in good_vs) + "）") if good_vs else "",
        len(bad_vs), ("（" + "、".join(r["hero"] for r in bad_vs) + "）") if bad_vs else ""))
    if src == "stratz" and sig["with"]:
        best = max(sig["with"], key=lambda r: r["winrate"] if r["winrate"] is not None else -1)
        worst = min(sig["with"], key=lambda r: r["winrate"] if r["winrate"] is not None else 999)
        P("  最搭队友：**%s**（%.1f%%）；最不搭：**%s**（%.1f%%）。" % (
            best["hero"], best["winrate"] or 0, worst["hero"], worst["winrate"] or 0))
    P("> 用法：这是**英雄层面的历史统计相性**（大样本先验），不代表本局必然；结合对线/团战实际数据一起看。"
      "被克多/协同差 = 选人先天吃亏（非战之罪的一个佐证）；占优却输 = 更该找自己或节奏问题。")

    emit(None, sig)

if __name__ == "__main__":
    main()
