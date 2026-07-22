#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dota2-match-review · 跨局趋势取数脚本（把单局复盘放进近况大背景）
用法:
    python3 fetch_trend.py [--account 137084212] [--last 20] [--append /path/_review_<id>.md] [--out /path/snip.md]

做的事（全部确定性，OpenDota，无需 token / 无需解析）:
    1) 拉该账号最近 N 局（含所有模式）
    2) 算：当前连胜/连败、近 N 局胜率、按英雄 W/L（专坑英雄 & 本命）、按天分布、平均时长、上头(tilt)检测
    3) 打印 "## 12. 跨局趋势（近况）" 数据卡 + trend 信号 JSON

价值：单局复盘容易只见树木；这一节把"这局"放进"最近这一串"里——
     一局输在非战之罪、但你已经 6 连败 + 反复拿某专坑英雄，那真正该改的是"别再上头/别再拿这英雄"。
     与 dota2-playtime 台账互补（那个管时长健康，这个管战绩趋势）。
仅用标准库(urllib)。需要联网。失败静默降级。
"""
import os, sys, json, argparse, datetime, urllib.request, urllib.error

OD = "https://api.opendota.com/api"
DEFAULT_ACCOUNT = 137084212

def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "dota2-match-review/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=DEFAULT_ACCOUNT)
    ap.add_argument("--last", type=int, default=20)
    ap.add_argument("--out", default=None)
    ap.add_argument("--append", default=None)
    a = ap.parse_args()

    out = []
    P = out.append
    P("## 12. 跨局趋势（近况 · 最近 %d 局）" % a.last)

    def emit(note=None, signals=None):
        if note: P(note)
        P("")
        P("### 趋势信号 JSON（供判断用，勿直接展示给用户）")
        P("```json")
        P(json.dumps(signals or {"source": "trend", "parsed": False}, ensure_ascii=False, indent=2))
        P("```")
        text = "\n".join(out)
        print(text)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f: f.write(text + "\n")
        if a.append and os.path.exists(a.append):
            with open(a.append, "a", encoding="utf-8") as f: f.write("\n" + text + "\n")
            print("\n[已把跨局趋势片段追加到 " + a.append + "]", file=sys.stderr)

    try:
        heroes = get(f"{OD}/heroes")
        LOC = {h["id"]: h["localized_name"] for h in heroes}
        ms = get(f"{OD}/players/{a.account}/matches?limit={a.last}&significant=0")
    except Exception as e:
        emit("> ⚠️ 拉取近况失败：" + str(e) + "。跳过跨局趋势。",
             {"source": "trend", "parsed": False, "reason": "fetch_failed"})
        return
    if not isinstance(ms, list) or not ms:
        emit("> ⚠️ 没有近况数据，跳过跨局趋势。", {"source": "trend", "parsed": False, "reason": "empty"})
        return

    # 每局：胜负 / 英雄 / 时长 / 日期 / KDA
    games = []
    for m in ms:
        if m.get("radiant_win") is None or m.get("player_slot") is None: continue
        won = (m["player_slot"] < 128) == m["radiant_win"]
        d = datetime.datetime.fromtimestamp(m["start_time"]).astimezone().date().isoformat() if m.get("start_time") else None
        games.append({"won": won, "hero": LOC.get(m.get("hero_id"), str(m.get("hero_id"))),
                      "dur": m.get("duration"), "date": d,
                      "kda": [m.get("kills"), m.get("deaths"), m.get("assists")], "lobby": m.get("lobby_type")})
    n = len(games)
    wins = sum(1 for g in games if g["won"])
    wr = round(100 * wins / n) if n else 0

    # 当前连胜/连败（从最近一局往回）
    streak_kind = "胜" if games[0]["won"] else "负"
    streak = 0
    for g in games:
        if g["won"] == games[0]["won"]: streak += 1
        else: break
    # 最长连败（近 N 局内）
    longest_loss = cur = 0
    for g in games:
        cur = cur + 1 if not g["won"] else 0
        longest_loss = max(longest_loss, cur)

    # 按英雄
    hero_stat = {}
    for g in games:
        h = hero_stat.setdefault(g["hero"], {"n": 0, "w": 0})
        h["n"] += 1; h["w"] += 1 if g["won"] else 0
    hero_rows = sorted(hero_stat.items(), key=lambda kv: -kv[1]["n"])
    trap = [(h, s) for h, s in hero_stat.items() if s["n"] >= 3 and s["w"] / s["n"] <= 0.34]   # 专坑
    core = [(h, s) for h, s in hero_stat.items() if s["n"] >= 3 and s["w"] / s["n"] >= 0.6]     # 本命

    # 按天（最近那天=今天视角）
    by_day = {}
    for g in games:
        if not g["date"]: continue
        dd = by_day.setdefault(g["date"], {"n": 0, "w": 0})
        dd["n"] += 1; dd["w"] += 1 if g["won"] else 0
    latest_day = games[0]["date"]
    today = by_day.get(latest_day, {"n": 0, "w": 0})

    # 上头(tilt)检测
    tilt = []
    if streak_kind == "负" and streak >= 3:
        tilt.append(f"当前 {streak} 连败")
    if today["n"] >= 6 and today["w"] / max(today["n"], 1) <= 0.4:
        tilt.append(f"最近一天打了 {today['n']} 局仅 {today['w']} 胜（越打越崩的典型）")
    if trap:
        tilt.append("反复拿专坑英雄：" + "、".join(f"{h}({s['w']}/{s['n']})" for h, s in trap))

    avg_dur = round(sum(g["dur"] for g in games if g["dur"]) / max(sum(1 for g in games if g["dur"]), 1))

    # ---------- 输出 ----------
    P("- 近 %d 局：**%d 胜 %d 负（胜率 %d%%）**；当前 **%d 连%s**；近段最长连败 %d" % (
        n, wins, n - wins, wr, streak, streak_kind, longest_loss))
    P("- 最近战绩(新→旧)：" + " ".join(("✅" if g["won"] else "❌") for g in games))
    P("- 平均时长 %d 分；最近一天(%s) %d 局 %d 胜" % (avg_dur // 60, latest_day, today["n"], today["w"]))
    P("- 英雄分布：" + " / ".join(f"{h} {s['w']}/{s['n']}" for h, s in hero_rows[:8]))
    if core: P("- 🟢 近段本命(≥3局胜率≥60%)：" + "、".join(f"{h}({s['w']}/{s['n']})" for h, s in core))
    if trap: P("- 🔴 近段专坑(≥3局胜率≤34%)：" + "、".join(f"{h}({s['w']}/{s['n']})" for h, s in trap))
    if tilt:
        P("- ⚠️ **上头信号**：" + "；".join(tilt) + " → 复盘单局输赢之外，**近况层面该提醒收手/换英雄**")
    else:
        P("- 近况平稳，无明显上头信号")
    P("> 用法：把'这局'放进'最近这一串'看——若这局判为非战之罪、但你已连败+反复拿专坑英雄，"
      "真正的改进是**近况层面的**（下号止损、换回本命/强势位），比只盯单局更有用。与 dota2-playtime 台账(管时长健康)互补。")

    signals = {
        "source": "trend", "parsed": True, "account": a.account, "n": n,
        "wins": wins, "losses": n - wins, "winrate": wr,
        "current_streak": streak, "streak_kind": streak_kind, "longest_loss_streak": longest_loss,
        "recent_results_new_to_old": [g["won"] for g in games],
        "avg_duration_min": round(avg_dur / 60, 1),
        "latest_day": latest_day, "latest_day_games": today["n"], "latest_day_wins": today["w"],
        "hero_breakdown": {h: {"games": s["n"], "wins": s["w"]} for h, s in hero_rows},
        "trap_heroes": [{"hero": h, "w": s["w"], "n": s["n"]} for h, s in trap],
        "core_heroes": [{"hero": h, "w": s["w"], "n": s["n"]} for h, s in core],
        "tilt_signals": tilt,
    }
    emit(None, signals)

if __name__ == "__main__":
    main()
