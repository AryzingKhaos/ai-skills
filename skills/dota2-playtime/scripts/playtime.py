#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dota2-playtime · 统计某账号 Dota2 的【真实消耗时间】+【健康预算评判】+【天梯上下分检查】
默认账号 137084212；默认窗口 = 当前自然周（本周一 00:00 → 现在，含今天）。

真实消耗时间 = Σ每局( 对局 duration + 选人时间(默认90s) + 匹配/赛后等开销(默认180s) )

健康预算（按自然周/7天，针对真实消耗时间）：
    推荐 8–10 小时（基准 10h）；>10h 警告；>12h 严重警告；>15h 必须强制停玩一周。

天梯目标：5000 分。每次执行检查本窗口"上下分"（按天梯胜负净场 × 每局约±25 估算；
    Valve 已隐藏精确 MMR，OpenDota 给不到具体分，故用段位 + 胜负净分估算，可用 --mmr 手填真实分精确化）。

数据源：OpenDota（无需 key）。仅标准库 urllib，需要联网。
用法：
    python3 playtime.py [--days N] [--account ID] [--pick-seconds 90] [--overhead-seconds 180]
                        [--mmr 当前真实分] [--target-mmr 5000] [--mmr-per-game 25] [--out PATH]
    不带 --days → 当前自然周；带 --days N → 过去 N 天（截止昨天 24:00，不含今天）。
"""
import sys, os, re, json, argparse, datetime, urllib.request, urllib.error

API = "https://api.opendota.com/api"
DEFAULT_ACCOUNT = 137084212
H = 3600
# 预算档位以「当周红线上限 cap」为基准按比例缩放。
# cap 默认 15h；台账当周自设「本周时限」则优先用它（--cap-hours 再优先）。
# 比例锚定：cap=15h 时复现原档位（推荐下限 8h / 基准 10h / 严重 12h / 红线 15h）。
DEFAULT_CAP_H = 15.0
LOW_R, BASE_R, SEVERE_R = 8.0 / 15, 10.0 / 15, 12.0 / 15
DEFAULT_LEDGER = "/Users/aaron/workspace/个人/生活/DOTA2时长.md"

def read_ledger_cap(path, monday_date):
    """从台账读当周（按周一日期前缀匹配「自然周」列）的「本周时限」小时数；无则 None。"""
    if not path or not os.path.exists(path):
        return None
    prefix = monday_date.isoformat()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s.startswith("|"):
                    continue
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) < 5 or not cells[0].startswith(prefix):
                    continue
                m = re.search(r"(\d+(?:\.\d+)?)\s*[hH]", cells[4])   # 第5列=本周时限，如 "15h"
                if m:
                    return float(m.group(1))
    except Exception:
        return None
    return None

def parse_hm_seconds(s):
    """解析 'XhYm' / 'Xh' / '-XhYm'(负结余=透支) 为秒；解析不了返回 None（'—'/'待结算' 等自然跳过）。"""
    m = re.search(r"(-|−)?\s*(\d+)\s*[hH](?:\s*(\d+)\s*[mM])?", s)
    if not m:
        return None
    sec = int(m.group(2)) * 3600 + int(m.group(3) or 0) * 60
    return -sec if m.group(1) else sec

def read_ledger_balance(path, monday_date):
    """从台账读可用结余（秒）：「当前结余量」列（第7列）中，周一日期早于本周、
    且值可解析（如 2h53m）的**最近**一行。结余规则 2026-06-22 生效，此前行为「—」自然跳过。"""
    if not path or not os.path.exists(path):
        return None
    best_date, best_val = None, None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s.startswith("|"):
                    continue
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) < 7:
                    continue
                m = re.match(r"(\d{4}-\d{2}-\d{2})", cells[0])
                if not m:
                    continue
                try:
                    wk = datetime.date.fromisoformat(m.group(1))
                except ValueError:
                    continue
                if wk >= monday_date:
                    continue
                sec = parse_hm_seconds(cells[6])
                if sec is None:
                    continue
                if best_date is None or wk > best_date:
                    best_date, best_val = wk, sec
    except Exception:
        return None
    return best_val

def fmt_h(x):
    return ("%d" % round(x)) if abs(x - round(x)) < 0.05 else ("%.1f" % x)

def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "dota2-playtime/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def fmt_dur(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    if h: return "%d 小时 %d 分钟" % (h, m)
    if m: return "%d 分钟 %d 秒" % (m, s)
    return "%d 秒" % s

LOBBY = {0: "普通匹配", 1: "练习", 2: "锦标赛", 4: "合作AI", 5: "车队", 6: "赛事",
         7: "天梯", 8: "1v1中路", 9: "勇士联赛", 12: "战队", 13: "新手"}
def lobby_name(n): return LOBBY.get(n, "模式%s" % n)

MEDAL = {1: "先锋(Herald)", 2: "卫士(Guardian)", 3: "中军(Crusader)", 4: "统帅(Archon)",
         5: "传奇(Legend)", 6: "万古(Ancient)", 7: "神话(Divine)", 8: "冠绝(Immortal)"}

def rank_tier_str(rt):
    if not rt: return ("未知", None, None)
    medal, star = rt // 10, rt % 10
    name = MEDAL.get(medal, "段位%s" % medal)
    return ("%s%s" % (name, (" %d 星" % star) if star else ""), medal, star)

def rank_tier_to_mmr(rt):
    """段位→MMR 粗估（OpenDota 无精确 MMR，按公开经验：每大段≈770、每星≈154）。仅估算。"""
    if not rt: return None
    medal, star = rt // 10, rt % 10
    if medal >= 8: return 5420  # 冠绝起步，约值
    return (medal - 1) * 770 + (max(star, 1) - 1) * 154

def mmr_to_medal_name(mmr):
    medal = min(8, mmr // 770 + 1)
    return MEDAL.get(medal, "?")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=DEFAULT_ACCOUNT)
    ap.add_argument("--days", type=int, default=None, help="过去N天(截止昨天24:00)；不给则按当前自然周")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--pick-seconds", type=int, default=90, dest="pick_seconds")
    ap.add_argument("--overhead-seconds", type=int, default=180, dest="overhead_seconds")
    ap.add_argument("--mmr", type=int, default=None, help="你的当前真实天梯分(手填则精确算距5000的差距与本窗口上下分)")
    ap.add_argument("--target-mmr", type=int, default=5000, dest="target_mmr")
    ap.add_argument("--mmr-per-game", type=int, default=25, dest="mmr_per_game", help="每局天梯胜负约±多少分(估算用)")
    ap.add_argument("--cap-hours", type=float, default=None, dest="cap_hours",
                    help="本周红线上限(小时)；覆盖台账/默认。各档位按此值比例缩放")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER, help="时长台账路径(自然周模式下读当周自设「本周时限」与「当前结余量」)")
    ap.add_argument("--balance-hours", type=float, default=None, dest="balance_hours",
                    help="手动指定可用结余(小时)；默认自然周模式从台账读最近已结算「当前结余量」")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    # ---- 窗口（本地时区）----
    now = datetime.datetime.now().astimezone()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if a.days is None:
        window_start = today0 - datetime.timedelta(days=today0.weekday())  # 本周一 00:00
        window_end = now                                                   # 含今天到现在
        mode_label = "本周（自然周，本周一 00:00 → 现在，含今天）"
        elapsed_days = now.weekday() + 1
    else:
        window_end = today0                                                # 昨天 24:00
        window_start = today0 - datetime.timedelta(days=a.days)
        mode_label = "过去 %d 天（截止昨天 24:00，不含今天）" % a.days
        elapsed_days = a.days
    ws, we = window_start.timestamp(), window_end.timestamp()
    tzname = now.tzname() or ""

    # ---- 本周时限（优先级：--cap-hours > 台账当周自设「本周时限」> 默认15h）----
    cap_source = "default"; cap_hours = None
    if a.cap_hours is not None:
        cap_hours, cap_source = a.cap_hours, "arg"
    elif a.days is None:                      # 仅自然周模式读台账当周设定
        lc = read_ledger_cap(a.ledger, window_start.date())
        if lc is not None:
            cap_hours, cap_source = lc, "ledger"
    if cap_hours is None:
        cap_hours = DEFAULT_CAP_H
    # 各档位（秒）按本周时限比例缩放
    low_h, base_h, severe_h = cap_hours * LOW_R, cap_hours * BASE_R, cap_hours * SEVERE_R
    low_s, base_s, warn_s, severe_s, cap_s = low_h * H, base_h * H, base_h * H, severe_h * H, cap_hours * H

    # ---- 可用结余 + 超限红线 = 本周时限 + 结余（2026-06-22 结余规则）----
    # 优先级：--balance-hours > 台账「当前结余量」（仅自然周模式）> 0
    bal_source = "none"; bal_s = 0.0
    if a.balance_hours is not None:
        bal_s, bal_source = a.balance_hours * H, "arg"
    elif a.days is None:
        lb = read_ledger_balance(a.ledger, window_start.date())
        if lb is not None:
            bal_s, bal_source = float(lb), "ledger"
    hard_s = cap_s + bal_s
    hard_h = hard_s / H

    # ---- 拉对局 ----
    try:
        matches = get("%s/players/%d/matches?limit=%d&significant=0" % (API, a.account, a.limit))
    except urllib.error.HTTPError as e:
        print("错误: OpenDota 请求失败 HTTP %d（账号 %d 是否存在/公开？）" % (e.code, a.account), file=sys.stderr); sys.exit(2)
    except Exception as e:
        print("错误: 网络请求失败: %s" % e, file=sys.stderr); sys.exit(2)
    if not isinstance(matches, list):
        print("错误: 返回格式异常: %s" % str(matches)[:200], file=sys.stderr); sys.exit(3)

    # ---- 段位（精确 MMR Valve 已隐藏，OpenDota 多为 None）----
    rank_tier = None
    try:
        rank_tier = (get("%s/players/%d" % (API, a.account)) or {}).get("rank_tier")
    except Exception:
        pass

    # ---- 过滤 + 汇总 ----
    inwin = [m for m in matches if m.get("start_time") is not None
             and ws <= m["start_time"] < we and m.get("duration") is not None]
    truncated = (len(matches) >= a.limit and matches and matches[-1].get("start_time") is not None
                 and matches[-1]["start_time"] > ws)
    total = sum(m["duration"] for m in inwin)
    n = len(inwin)
    per_extra = a.pick_seconds + a.overhead_seconds
    pick_total, oh_total = n * a.pick_seconds, n * a.overhead_seconds
    real_total = total + pick_total + oh_total

    by_day, by_lobby = {}, {}
    rwin = rloss = 0  # 天梯(lobby 7)胜负
    for m in inwin:
        d = datetime.datetime.fromtimestamp(m["start_time"]).astimezone().date().isoformat()
        by_day.setdefault(d, {"n": 0, "sec": 0}); by_day[d]["n"] += 1; by_day[d]["sec"] += m["duration"]
        ln = lobby_name(m.get("lobby_type"))
        by_lobby.setdefault(ln, {"n": 0, "sec": 0}); by_lobby[ln]["n"] += 1; by_lobby[ln]["sec"] += m["duration"]
        if m.get("lobby_type") == 7 and m.get("radiant_win") is not None and m.get("player_slot") is not None:
            won = (m["player_slot"] < 128) == m["radiant_win"]
            if won: rwin += 1
            else: rloss += 1

    # ---- 预算评判 ----
    # 主判据 = 「时限 + 结余」超限阶梯（唯一决定状态 emoji）：
    #   x>红线+5h → 🛑；x>红线+2h → 🚨；x>红线 → ⛔；时限<x≤红线 → 💰；x≤时限 → ✅
    # ✅ 内部再按接近推荐基准/时限的程度给不同措辞——但 emoji 一律 ✅，绝不在未超红线时误报 🚨/⚠️。
    cap_tag = "（当周自设）" if cap_source == "ledger" else ("（命令行设定）" if cap_source == "arg" else "")
    over_hard = real_total - hard_s   # 超出「时限+结余」红线的量
    if over_hard >= 5 * H:
        status = "🛑 **触发下周强制停玩**：已超「时限+结余」超限红线 **%s**（≥5h）—— 下一周强制停玩一周（停玩周的时限照常计入结余）；超出部分记为**负结余（透支）**。" \
                 % fmt_dur(over_hard)
    elif over_hard >= 2 * H:
        status = "🚨 **强烈警告**：已超「时限+结余」超限红线 **%s**（≥2h），超出部分记为**负结余（透支）**；超到 5h 将触发下周强制停玩。" \
                 % fmt_dur(over_hard)
    elif over_hard >= 0:
        status = "⛔ **超限警告**：已超「时限+结余」超限红线 **%s**，超出部分记为**负结余（透支惩罚）**；超 2h 强烈警告、超 5h 下周强制停玩。" \
                 % fmt_dur(over_hard)
    elif real_total > cap_s:
        # 时限 < 消耗 ≤ 红线：合法动用结余（此分支仅当 bal_s>0 才可达）
        status = "💰 **动用结余中**：已超本周时限 %sh%s，超出的 %s 由结余支付（合法消耗）；结余还可支付 %s，之后才进入透支惩罚区。" \
                 % (fmt_h(cap_hours), cap_tag, fmt_dur(real_total - cap_s), fmt_dur(hard_s - real_total))
    else:
        # 消耗 ≤ 本周时限 → 一律 ✅（既未超时限、更远未及红线）；按接近程度给措辞，emoji 不变
        if real_total >= severe_s:
            status = "✅ **未超本周时限**（更远未及超限红线），但已进入高消耗区：超推荐基准 %s、距时限仅剩 %s，建议见好就收。" \
                     % (fmt_dur(real_total - base_s), fmt_dur(cap_s - real_total))
        elif real_total >= warn_s:
            status = "✅ 未超本周时限，已过推荐基准 %s，建议本周别再多打。" % fmt_dur(real_total - base_s)
        elif real_total >= low_s:
            status = "✅ 接近推荐上限（%s–%sh 区间内），可以再来一两把就收。" % (fmt_h(low_h), fmt_h(base_h))
        else:
            status = "✅ 健康范围（未到 %sh 推荐区间下限），还很充裕。" % fmt_h(low_h)
    rem_base = base_s - real_total   # 距 推荐基准
    rem_cap = cap_s - real_total     # 距 本周时限
    rem_hard = hard_s - real_total   # 距 超限红线（时限+结余）

    # ---- 天梯上下分 ----
    base_mmr = a.mmr if a.mmr is not None else rank_tier_to_mmr(rank_tier)
    mmr_is_estimate = a.mmr is None
    net_games = rwin - rloss
    est_delta = net_games * a.mmr_per_game

    out = []
    P = out.append
    P("# Dota2 真实消耗 + 健康预算 + 天梯进度 · 账号 %d（%s）" %
      (a.account, rank_tier_str(rank_tier)[0]))
    P("")
    P("- **统计窗口**：%s ～ %s（%s，本地 %s）"
      % (window_start.strftime("%Y-%m-%d %H:%M"), window_end.strftime("%Y-%m-%d %H:%M"), mode_label, tzname))
    if truncated:
        P("- ⚠️ **可能未取全**：对局数达上限 %d 且未回溯到窗口起点，请加大 `--limit`。" % a.limit)

    # 时长 + 预算
    P("")
    P("## ⏱️ 时长与健康预算")
    P("- 🎮 **真实消耗时间：%s**（共 %d 局，日均 %s）" % (fmt_dur(real_total), n, fmt_dur(real_total / max(elapsed_days, 1))))
    P("- %s" % status)
    if rem_base > 0:
        P("- 距 **%s 小时推荐基准**还可玩 **%s**（仍在推荐范围内）。" % (fmt_h(base_h), fmt_dur(rem_base)))
    else:
        P("- 已超 %s 小时推荐基准 **%s**。" % (fmt_h(base_h), fmt_dur(-rem_base)))
    cap_name = "当周自设时限" if cap_source == "ledger" else ("命令行时限" if cap_source == "arg" else "默认时限")
    if rem_cap > 0:
        P("- 距 **%s 小时%s**还剩 **%s**。" % (fmt_h(cap_hours), cap_name, fmt_dur(rem_cap)))
    else:
        P("- 已超 %s 小时%s **%s**。" % (fmt_h(cap_hours), cap_name, fmt_dur(-rem_cap)))
    bal_name = {"ledger": "台账结余", "arg": "命令行结余"}.get(bal_source)
    bal_disp = ("-" if bal_s < 0 else "") + fmt_dur(abs(bal_s))
    if bal_name:
        if bal_s < 0:
            P("- ⚠️ 可用结余为**负（负债 %s，%s，上周透支）** → **本周可用总量 = 时限 − 负债 = %s 小时（超限红线）**。"
              % (fmt_dur(-bal_s), bal_name, fmt_h(hard_h)))
        else:
            P("- 可用结余 **%s**（%s）→ **本周可用总量 %s 小时（超限红线）**。" % (bal_disp, bal_name, fmt_h(hard_h)))
        if rem_hard > 0:
            P("- 距超限红线还剩 **%s**。" % fmt_dur(rem_hard))
        else:
            P("- 已超超限红线 **%s**，超出部分将计为负结余（透支）；超 2h 🚨、超 5h 🛑 下周强制停玩。" % fmt_dur(-rem_hard))
    elif rem_hard <= 0:
        P("- 无可用结余，已超超限红线 **%s**，超出部分将计为负结余（透支）；超 2h 🚨、超 5h 🛑 下周强制停玩。" % fmt_dur(-rem_hard))
    P("")
    P("| 时长构成 | 口径 | 时长 |")
    P("|---|---|---|")
    P("| 纯对局 | 每局 duration 相加 | %s |" % fmt_dur(total))
    P("| 选人 | %d 局 × %d 秒 | %s |" % (n, a.pick_seconds, fmt_dur(pick_total)))
    P("| 匹配/赛后等 | %d 局 × %d 秒 | %s |" % (n, a.overhead_seconds, fmt_dur(oh_total)))
    P("| **真实消耗** | 三项之和 | **%s** |" % fmt_dur(real_total))
    P("")
    P("> 预算口径：**时限内（≤%sh 时限%s）一律 ✅**（推荐下限 %sh／基准 %sh 仅作『接近程度』提示，不触发警告 emoji）；**超限红线 = 时限 + 可用结余 = %sh**——超时限但在结余内 = 💰 合法动用结余；**超红线 ⛔ 透支、超红线+2h 🚨 强烈警告、超红线+5h 🛑 下周强制停玩**（停玩周时限照常入结余）。针对真实消耗时间。"
      % (fmt_h(cap_hours), cap_tag, fmt_h(low_h), fmt_h(base_h), fmt_h(hard_h)))

    # 天梯进度
    P("")
    P("## 🏆 天梯进度（目标 %d 分）" % a.target_mmr)
    P("- 当前段位：**%s**（rank_tier=%s）" % (rank_tier_str(rank_tier)[0], rank_tier))
    if base_mmr is not None:
        tag = "（你手填）" if not mmr_is_estimate else "（段位粗估，Valve 已隐藏精确 MMR）"
        gap = a.target_mmr - base_mmr
        P("- 当前分数：约 **%d 分**%s" % (base_mmr, tag))
        if gap > 0:
            P("- 距目标 **%d 分**：还差约 **%d 分**（≈ %s 段，约 %.1f 个大段）" %
              (a.target_mmr, gap, mmr_to_medal_name(a.target_mmr), gap / 770.0))
        else:
            P("- 🎉 已达成目标 %d 分（超出约 %d 分）！" % (a.target_mmr, -gap))
    P("- **本窗口上下分**：天梯 **%d 胜 %d 负**%s，净 **%+d 场** → 估算 **%+d 分**（按每局约 ±%d）" %
      (rwin, rloss, ("（胜率 %d%%）" % round(100 * rwin / (rwin + rloss))) if (rwin + rloss) else "",
       net_games, est_delta, a.mmr_per_game))
    if a.mmr is not None:
        P("  - 据此本窗口后约 **%d 分**，距目标还差约 **%d 分**。" % (a.mmr + est_delta, a.target_mmr - (a.mmr + est_delta)))
    if rwin + rloss == 0:
        P("  - 本窗口没有天梯对局。")
    P("")
    P("> 说明：OpenDota 拿不到精确 MMR（Valve 已隐藏），故『上下分』按天梯胜负净场 × 每局约 ±%d 估算；想精确就用 `--mmr 你的真实分`。" % a.mmr_per_game)

    # 明细
    if inwin:
        P("")
        P("## 按天")
        P("| 日期 | 局数 | 纯对局 | 真实消耗 |")
        P("|---|---|---|---|")
        for d in sorted(by_day):
            gd, nd = by_day[d]["sec"], by_day[d]["n"]
            P("| %s | %d | %s | %s |" % (d, nd, fmt_dur(gd), fmt_dur(gd + nd * per_extra)))
        P("")
        P("## 按模式（纯对局时长）")
        P("| 模式 | 局数 | 纯对局 |")
        P("|---|---|---|")
        for ln in sorted(by_lobby, key=lambda k: -by_lobby[k]["sec"]):
            P("| %s | %d | %s |" % (ln, by_lobby[ln]["n"], fmt_dur(by_lobby[ln]["sec"])))

    signals = {
        "account": a.account, "rank_tier": rank_tier, "rank_name": rank_tier_str(rank_tier)[0],
        "window_local": [window_start.strftime("%Y-%m-%d %H:%M:%S"), window_end.strftime("%Y-%m-%d %H:%M:%S")],
        "mode": "natural_week" if a.days is None else ("last_%d_days" % a.days),
        "tz": tzname, "match_count": n,
        "pick_seconds": a.pick_seconds, "overhead_seconds": a.overhead_seconds,
        "pure_game_seconds": total, "real_total_seconds": real_total, "real_total_human": fmt_dur(real_total),
        "budget": {
            "cap_hours": cap_hours, "cap_source": cap_source,
            "rec_low_h": round(low_h, 2), "rec_base_h": round(base_h, 2), "warn_h": round(base_h, 2),
            "severe_h": round(severe_h, 2), "cap_h": cap_hours,
            "balance_seconds": int(bal_s), "balance_human": bal_disp, "balance_source": bal_source,
            "hard_cap_hours": round(hard_h, 2),
            "over_base": real_total >= warn_s, "over_severe": real_total >= severe_s,
            "over_limit_using_balance": real_total >= cap_s and bal_s > 0 and real_total < hard_s,
            "over_hard_cap": over_hard >= 0,
            "over_hard_severe": over_hard >= 2 * H,
            "forced_stop_next_week": over_hard >= 5 * H,
            "over_cap_force_stop": over_hard >= 5 * H,
            "remaining_to_base_seconds": rem_base, "remaining_to_cap_seconds": rem_cap,
            "remaining_to_hard_cap_seconds": rem_hard,
        },
        "ladder": {
            "target_mmr": a.target_mmr, "current_mmr_estimate": base_mmr, "mmr_is_estimate": mmr_is_estimate,
            "ranked_win": rwin, "ranked_loss": rloss, "net_games": net_games,
            "est_window_mmr_delta": est_delta, "mmr_per_game": a.mmr_per_game,
        },
        "by_day": {d: by_day[d] for d in sorted(by_day)},
    }
    P("")
    P("```json")
    P(json.dumps(signals, ensure_ascii=False, indent=2))
    P("```")

    text = "\n".join(out)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("\n[已保存到 %s]" % a.out, file=sys.stderr)

if __name__ == "__main__":
    main()
