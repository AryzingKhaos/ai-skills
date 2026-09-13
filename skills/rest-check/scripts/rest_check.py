#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rest-check · 请假触发判定器

设计出处：/Users/aaron/workspace/个人/人生规划/人生游戏/休息机制.md

这个脚本的唯一职责是【判定】，不提供建议、不接受申辩。
人只负责执行结果，不参与判定 —— 见机制文档第五节。

用法：
    python3 rest_check.py                     # 判定：今天该不该请假
    python3 rest_check.py --status            # 只看配额/基线/冷却，不判定、不写历史
    python3 rest_check.py --take 2026-09-05 --rule 4 --note "强制支取"
                                              # 记录一天请假日已支取
    python3 rest_check.py --undo 2026-09-05   # 撤销误记的支取
    python3 rest_check.py --json              # 判定结果输出 JSON
    python3 rest_check.py --no-log            # 判定但不写入判定历史
"""

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys

WORK_FILE = "/Users/aaron/workspace/个人/生活/工作时长.md"
DOTA_FILE = "/Users/aaron/workspace/个人/生活/DOTA2时长.md"
LEDGER_FILE = "/Users/aaron/workspace/个人/生活/休息台账.md"

# --- 机制常量（改动请先改机制文档，再改这里）------------------------------
THRESHOLD_RATIO = 1.25       # 第 1/3 条：阈值 = 工作项目基线 × 1.25
RULE1_WEEKS = 2              # 第 1 条：连续 N 个完整周超阈值
RULE2_DAYS = 21              # 第 2 条：连续 N 天没有一整天完全不工作
QUARTER_QUOTA_MIN = 1        # 第 4 条：每季度至少支取（配额是下限）
QUARTER_QUOTA_MAX = 2        # 每季度最多支取（配额有上限，防失控）
COOLDOWN_DAYS = 14           # 冷却期：距上次支取不足 N 天不再触发
BASELINE_WINDOW = 12         # 基线样本窗口（完整周）
STALE_DAYS = 2               # 数据超过 N 天没更新就告警
SEED_BASELINE_MIN = 17 * 60 + 43   # 机制文档第六节记录的初始基线 17h43m


# ---------------------------------------------------------------- 基础工具

def parse_dur(text):
    """把时长文本解析成分钟。兼容 '17h52m' / '17 小时 52 分钟' / '43 分钟' / '2h'。

    ⚠️ DOTA2时长.md 从 2026-08-17 起格式由 17h52m 变为 17 小时 52 分钟，
    只匹配 \\d+h\\d+m 会静默丢行 —— 两种都必须吃下。
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s or s in ("—", "-", "待结算"):
        return None
    s = s.replace("小時", "小时").replace("分鐘", "分钟")

    m = re.search(r"(\d+)\s*h\s*(\d+)\s*m", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+)\s*小时\s*(\d+)\s*分钟", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+)\s*小时", s)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*分钟", s)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+)\s*h", s)
    if m:
        return int(m.group(1)) * 60
    return None


def fmt_dur(minutes):
    if minutes is None:
        return "—"
    neg = minutes < 0
    minutes = abs(int(round(minutes)))
    return "%s%dh%02dm" % ("-" if neg else "", minutes // 60, minutes % 60)


def parse_date(text):
    """'2026-09-03 周四' / '2026-09-03' -> date"""
    if not text:
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(text))
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_week(text):
    """'2026-08-31 ~ 2026-09-06' -> (date, date)"""
    ds = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", str(text or ""))
    if len(ds) < 2:
        return None
    a, b = parse_date(ds[0]), parse_date(ds[1])
    if a and b:
        return (a, b)
    return None


def read_text(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def split_cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_separator(line):
    return bool(re.fullmatch(r"\|[\s:\-|]+\|?", line.strip()))


def section_rows(text, heading_keyword):
    """取指定 ## 小节下的表格数据行（不含表头和分隔行）。"""
    if not text:
        return []
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("#") and heading_keyword in ln:
            start = i + 1
            break
    if start is None:
        return []
    rows = []
    header_seen = False
    for ln in lines[start:]:
        if ln.startswith("#"):
            break
        if not ln.strip().startswith("|"):
            continue
        if is_separator(ln):
            continue
        cells = split_cells(ln)
        if not header_seen:
            header_seen = True          # 第一行是表头
            continue
        rows.append(cells)
    return rows


def cell(row, idx):
    return row[idx] if idx < len(row) else ""


# ---------------------------------------------------------------- 读数据源

def load_work(path):
    """读工作时长台账：周汇总 + 有活动的日期集合（工作项目 + planB + 手动打卡）。"""
    text = read_text(path)
    if text is None:
        raise SystemExit("找不到工作时长台账：%s" % path)

    weeks = []
    for row in section_rows(text, "周汇总"):
        rng = parse_week(cell(row, 0))
        if not rng:
            continue
        weeks.append({
            "start": rng[0],
            "end": rng[1],
            "label": cell(row, 0),
            "work_min": parse_dur(cell(row, 1)),
            "planb_min": parse_dur(cell(row, 3)),
            "updated": parse_date(cell(row, 9)),
        })
    weeks.sort(key=lambda w: w["start"])

    # 「不工作」口径：工作项目 + planB 都没有记录才算休息日；其他项目不计。
    active = set()
    for heading in ("工作项目时间段", "planB项目时间段", "手动打卡明细"):
        for row in section_rows(text, heading):
            d = parse_date(cell(row, 0))
            if d:
                active.add(d)

    return weeks, active


def load_dota(path):
    """读 DOTA2 台账，返回 {周起始日: {...}}，含「是否超限」判定。"""
    text = read_text(path)
    if text is None:
        raise SystemExit("找不到 DOTA2 台账：%s" % path)

    out = {}
    for row in section_rows(text, "DOTA2 时长台账"):
        rng = parse_week(cell(row, 0))
        if not rng:
            continue
        status = cell(row, 5)
        out[rng[0]] = {
            "label": cell(row, 0),
            "spent_min": parse_dur(cell(row, 1)),
            "limit_min": parse_dur(cell(row, 4)),
            "status": status,
            "over": classify_over(status),
            "updated": parse_date(cell(row, 7)),
        }
    return out


def classify_over(status):
    """复用台账既有的「是否超限」列，不新造阈值。

    未超 / 动用结余（合法）-> False；已超 / ⛔🚨🛑 -> True；认不出来 -> None。
    """
    s = (status or "").strip()
    if not s or s == "—":
        return None
    if "未超" in s or "✅" in s:
        return False
    if "动用结余" in s or "💰" in s:
        return False            # 花结余是合法提取，不算超限
    if "已超" in s or "超限" in s or any(e in s for e in ("⛔", "🚨", "🛑", "⚠️")):
        return True
    return None


# ------------------------------------------------------------------ 台账

LEDGER_TEMPLATE = """# 休息台账

> 由 `rest-check` skill 自动维护。**这是数据，不是设计。**
> 机制设计、规则含义、为什么这么定，全部见
> `/Users/aaron/workspace/个人/人生规划/人生游戏/休息机制.md`。
> 改规则去改机制文档，不要改这个文件。

## 基线（只降不升）

| 指标 | 当前基线 | 请假阈值 | 样本周数 | 最后重算 | 说明 |
|---|---|---|---|---|---|
{baseline_rows}

## 请假支取记录

> 「配额是下限，不是上限」：每季度至少 {qmin} 天、最多 {qmax} 天。
> 季度末没用完是警报，不是成就。

| 日期 | 季度 | 触发条件 | 记录时间 | 备注 |
|---|---|---|---|---|
{take_rows}

## 判定历史

> 每跑一次 `rest-check` 追加一行。留着是为了让「这周特殊」无处藏身。

| 运行日期 | 结论 | 触发 | 近{n}完整周工作项目 | DOTA超限 | 无恢复窗口 | 本季度已用 |
|---|---|---|---|---|---|---|
{log_rows}
"""


def load_ledger(path):
    text = read_text(path)
    state = {"baseline_min": None, "baseline_weeks": 0, "baseline_date": None,
             "takes": [], "log": []}
    if text is None:
        return state

    for row in section_rows(text, "基线"):
        if "工作项目" in cell(row, 0):
            state["baseline_min"] = parse_dur(cell(row, 1))
            m = re.search(r"\d+", cell(row, 3))
            state["baseline_weeks"] = int(m.group(0)) if m else 0
            state["baseline_date"] = parse_date(cell(row, 4))

    for row in section_rows(text, "请假支取记录"):
        d = parse_date(cell(row, 0))
        if d:
            state["takes"].append({
                "date": d, "quarter": cell(row, 1), "rule": cell(row, 2),
                "recorded": cell(row, 3), "note": cell(row, 4),
            })
    state["takes"].sort(key=lambda t: t["date"])

    for row in section_rows(text, "判定历史"):
        if parse_date(cell(row, 0)):
            state["log"].append(row)

    return state


def save_ledger(path, state):
    baseline_rows = "| 工作项目 | %s | %s | %d | %s | 阈值 = 基线 × %s；只降不升 |" % (
        fmt_dur(state["baseline_min"]),
        fmt_dur(threshold_of(state["baseline_min"])),
        state["baseline_weeks"],
        state["baseline_date"].isoformat() if state["baseline_date"] else "—",
        THRESHOLD_RATIO,
    )

    if state["takes"]:
        take_rows = "\n".join(
            "| %s | %s | %s | %s | %s |" % (
                t["date"].isoformat(), t["quarter"], t["rule"], t["recorded"], t["note"])
            for t in state["takes"])
    else:
        take_rows = "| — | — | — | — | 尚无支取记录 |"

    log_rows = "\n".join("| " + " | ".join(r) + " |" for r in state["log"][-200:]) \
        if state["log"] else "| — | — | — | — | — | — | — |"

    body = LEDGER_TEMPLATE.format(
        baseline_rows=baseline_rows, take_rows=take_rows, log_rows=log_rows,
        qmin=QUARTER_QUOTA_MIN, qmax=QUARTER_QUOTA_MAX, n=RULE1_WEEKS)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)


# ------------------------------------------------------------------ 季度

def quarter_of(d):
    return (d.month - 1) // 3 + 1


def quarter_label(d):
    return "%dQ%d" % (d.year, quarter_of(d))


def quarter_bounds(d):
    q = quarter_of(d)
    start = dt.date(d.year, 3 * q - 2, 1)
    if q == 4:
        end = dt.date(d.year, 12, 31)
    else:
        end = dt.date(d.year, 3 * q + 1, 1) - dt.timedelta(days=1)
    return start, end


def quarter_progress(today):
    start, end = quarter_bounds(today)
    total = (end - start).days + 1
    done = (today - start).days + 1
    return done / float(total), start, end


# ------------------------------------------------------------------ 判定

def threshold_of(baseline_min):
    if baseline_min is None:
        return None
    return int(round(baseline_min * THRESHOLD_RATIO))


def recompute_baseline(weeks, state, today):
    """基线只降不升：算出来更低就采纳，更高就不动。"""
    complete = [w for w in weeks if w["end"] < today and w["work_min"] is not None]
    sample = complete[-BASELINE_WINDOW:]
    note = None

    if not sample:
        if state["baseline_min"] is None:
            state["baseline_min"] = SEED_BASELINE_MIN
            state["baseline_weeks"] = 0
            state["baseline_date"] = today
            note = "无完整周样本，用机制文档记录的初始基线 %s" % fmt_dur(SEED_BASELINE_MIN)
        return note

    # 向下取整而非四舍五入：基线宁低勿高（低基线 = 低阈值 = 更容易触发休息），
    # 也正好复现机制文档第六节记录的 17h43m。
    candidate = int(statistics.median([w["work_min"] for w in sample]))

    if state["baseline_min"] is None:
        state["baseline_min"] = candidate
        state["baseline_weeks"] = len(sample)
        state["baseline_date"] = today
        note = "首次建立基线：%d 个完整周中位数 = %s" % (len(sample), fmt_dur(candidate))
    elif candidate < state["baseline_min"]:
        old = state["baseline_min"]
        state["baseline_min"] = candidate
        state["baseline_weeks"] = len(sample)
        state["baseline_date"] = today
        note = "基线下调 %s → %s（%d 周中位数）" % (fmt_dur(old), fmt_dur(candidate), len(sample))
    elif candidate > state["baseline_min"]:
        note = ("算出的中位数 %s 高于当前基线 %s，按「只降不升」不采纳"
                "（适应了更高工作量不是基线该涨，那是慢性透支）"
                % (fmt_dur(candidate), fmt_dur(state["baseline_min"])))
        state["baseline_weeks"] = len(sample)
    else:
        state["baseline_weeks"] = len(sample)
    return note


def no_rest_streak(active_days, today):
    """从昨天往回数，连续多少天没有一整天完全不工作。"""
    if not active_days:
        return 0, False
    first = min(active_days)
    streak = 0
    d = today - dt.timedelta(days=1)
    while d >= first:
        if d in active_days:
            streak += 1
            d -= dt.timedelta(days=1)
        else:
            return streak, False
    return streak, True      # True = 数到数据起点了，可能被截断


def evaluate(today, weeks, active_days, dota, state):
    complete = [w for w in weeks if w["end"] < today]
    recent = complete[-RULE1_WEEKS:]
    threshold = threshold_of(state["baseline_min"])

    r = {"today": today, "threshold_min": threshold,
         "baseline_min": state["baseline_min"], "warnings": [], "rules": []}

    # 数据新鲜度
    for name, weeks_or_none, path in (("工作时长", weeks, WORK_FILE),):
        ups = [w["updated"] for w in weeks_or_none if w.get("updated")]
        if ups and (today - max(ups)).days > STALE_DAYS:
            r["warnings"].append("%s 台账最后更新于 %s，已 %d 天未刷新 —— 先跑 /work-time"
                                 % (name, max(ups).isoformat(), (today - max(ups)).days))
    dups = [v["updated"] for v in dota.values() if v.get("updated")]
    if dups and (today - max(dups)).days > STALE_DAYS:
        r["warnings"].append("DOTA2 台账最后更新于 %s，已 %d 天未刷新 —— 先跑 /dota2-playtime"
                             % (max(dups).isoformat(), (today - max(dups)).days))

    # --- 第 1 条：连续 2 周工作项目超阈值 ---------------------------------
    r["recent_weeks"] = [
        {"label": w["label"], "work_min": w["work_min"],
         "over": (w["work_min"] is not None and threshold is not None
                  and w["work_min"] >= threshold)}
        for w in recent
    ]
    hit1 = (len(recent) >= RULE1_WEEKS
            and all(x["over"] for x in r["recent_weeks"]))
    if len(recent) < RULE1_WEEKS:
        r["warnings"].append("完整周样本不足 %d 周，第 1 条无法判定" % RULE1_WEEKS)
    if hit1:
        r["rules"].append((1, "连续 %d 个完整周工作项目 ≥ %s：%s"
                           % (RULE1_WEEKS, fmt_dur(threshold),
                              "、".join("%s %s" % (x["label"], fmt_dur(x["work_min"]))
                                        for x in r["recent_weeks"]))))

    # --- 第 2 条：连续 21 天无恢复窗口 ------------------------------------
    streak, truncated = no_rest_streak(active_days, today)
    r["streak"] = streak
    r["streak_truncated"] = truncated
    hit2 = streak >= RULE2_DAYS
    if truncated and not hit2:
        r["warnings"].append("连续工作天数数到了数据起点（%d 天），真实值可能更长"
                             % streak)
    if hit2:
        r["rules"].append((2, "已连续 %d 天没有一整天完全不工作（阈值 %d 天，含周末）"
                           % (streak, RULE2_DAYS)))

    # --- 第 3 条：工作超载 AND DOTA 超限（麻醉信号）------------------------
    last = complete[-1] if complete else None
    r["last_week"] = None
    hit3 = False
    if last:
        dv = dota.get(last["start"])
        work_over = (last["work_min"] is not None and threshold is not None
                     and last["work_min"] >= threshold)
        dota_over = dv["over"] if dv else None
        r["last_week"] = {
            "label": last["label"], "work_min": last["work_min"],
            "work_over": work_over,
            "dota_status": dv["status"] if dv else "（该周无 DOTA 记录）",
            "dota_over": dota_over,
        }
        if dv is None:
            r["warnings"].append("最近完整周 %s 在 DOTA2 台账里没有对应行，第 3 条按「未超限」处理"
                                 % last["label"])
        elif dota_over is None:
            r["warnings"].append("看不懂 DOTA2 台账的「是否超限」写法：%s" % dv["status"])
        hit3 = bool(work_over and dota_over)
        if hit3:
            r["rules"].append((3, "麻醉信号：%s 工作项目 %s ≥ %s，同周 DOTA %s"
                               % (last["label"], fmt_dur(last["work_min"]),
                                  fmt_dur(threshold), dv["status"])))

    # --- 配额与冷却 -------------------------------------------------------
    qlabel = quarter_label(today)
    used = [t for t in state["takes"] if t["quarter"] == qlabel]
    r["quarter"] = qlabel
    r["used"] = len(used)
    r["quota_min"] = QUARTER_QUOTA_MIN
    r["quota_max"] = QUARTER_QUOTA_MAX

    pct, qstart, qend = quarter_progress(today)
    r["quarter_pct"] = pct
    r["quarter_range"] = (qstart, qend)

    last_take = state["takes"][-1]["date"] if state["takes"] else None
    r["last_take"] = last_take
    if last_take:
        since = (today - last_take).days
        r["cooldown_left"] = max(0, COOLDOWN_DAYS - since)
    else:
        r["cooldown_left"] = 0

    # --- 第 4 条：季度过半、配额一天没用 → 强制支取 ------------------------
    hit4 = (pct >= 0.5 and len(used) == 0)
    if hit4:
        r["rules"].append((4, "%s 已过 %.0f%%，请假配额 0/%d 未支取 → 强制支取"
                           "（配额是下限，季度末没用完是警报）"
                           % (qlabel, pct * 100, QUARTER_QUOTA_MIN)))

    # --- 结论 -------------------------------------------------------------
    if not r["rules"]:
        r["verdict"] = "no"
        r["headline"] = "今天不该请假"
        r["action"] = "照常上班。下次判定继续跑 /rest-check，不需要你判断。"
    elif r["cooldown_left"] > 0:
        r["verdict"] = "cooldown"
        r["headline"] = "触发了，但在冷却期内 —— 今天不请假"
        r["action"] = ("距上次支取（%s）还差 %d 天满 %d 天冷却期。冷却结束后再跑一次。"
                       % (last_take.isoformat(), r["cooldown_left"], COOLDOWN_DAYS))
    elif len(used) >= QUARTER_QUOTA_MAX:
        r["verdict"] = "exhausted"
        r["headline"] = "触发了，但本季度请假配额已用尽 —— 改用降载日"
        r["action"] = ("%s 已支取 %d/%d 天。今天不请假，改排一个降载日："
                       "照常上班，下班后不 build、不健身、不学习。"
                       % (qlabel, len(used), QUARTER_QUOTA_MAX))
    else:
        r["verdict"] = "yes"
        r["headline"] = "今天该请假"
        r["action"] = ("提交年假，然后跑："
                       "python3 rest_check.py --take %s --rule %d"
                       % (today.isoformat(), r["rules"][0][0]))
    return r


# ------------------------------------------------------------------ 输出

def render(r, baseline_note):
    L = []
    W = 60
    L.append("=" * W)
    L.append("  rest-check · %s · %s" % (r["today"].isoformat(), r["quarter"]))
    L.append("=" * W)
    L.append("")

    icon = {"yes": "🛑", "no": "✅", "cooldown": "🧊", "exhausted": "⛔"}[r["verdict"]]
    L.append("结论：%s %s" % (icon, r["headline"]))
    L.append("")
    L.append("→ %s" % r["action"])
    L.append("")

    if r["rules"]:
        L.append("命中的触发条件（OR 逻辑，任一满足即触发）")
        for num, desc in r["rules"]:
            L.append("  [第 %d 条] %s" % (num, desc))
    else:
        L.append("四条触发条件均未命中。")
    L.append("")

    L.append("-" * W)
    L.append("读数")
    L.append("  基线（工作项目，只降不升）  %s" % fmt_dur(r["baseline_min"]))
    L.append("  请假阈值 = 基线 × %s        %s" % (THRESHOLD_RATIO, fmt_dur(r["threshold_min"])))
    if r["recent_weeks"]:
        L.append("  近 %d 个完整周工作项目：" % len(r["recent_weeks"]))
        for x in r["recent_weeks"]:
            L.append("    %s  %s  %s" % (x["label"], fmt_dur(x["work_min"]),
                                         "≥阈值 ⚠️" if x["over"] else "未达阈值"))
    tail = "（数据起点截断，真实值可能更长）" if r.get("streak_truncated") else ""
    L.append("  连续无恢复窗口天数          %d / %d 天%s"
             % (r["streak"], RULE2_DAYS, tail))
    if r["last_week"]:
        lw = r["last_week"]
        L.append("  最近完整周 DOTA 是否超限    %s" % lw["dota_status"])
    L.append("")

    L.append("  本季度 %s（%s ~ %s，已过 %.0f%%）"
             % (r["quarter"], r["quarter_range"][0].isoformat(),
                r["quarter_range"][1].isoformat(), r["quarter_pct"] * 100))
    L.append("    请假已支取                %d 天（下限 %d，上限 %d）"
             % (r["used"], r["quota_min"], r["quota_max"]))
    if r["last_take"]:
        L.append("    上次支取                  %s（冷却剩 %d 天）"
                 % (r["last_take"].isoformat(), r["cooldown_left"]))
    else:
        L.append("    上次支取                  无记录")
    L.append("")

    if baseline_note:
        L.append("基线：%s" % baseline_note)
        L.append("")
    if r["warnings"]:
        L.append("⚠️  警告")
        for w in r["warnings"]:
            L.append("    - %s" % w)
        L.append("")

    L.append("=" * W)
    L.append("判定由脚本做，不由人做。不接受「再看看」「这周特殊」。")
    L.append("=" * W)
    return "\n".join(L)


def log_row(r):
    weeks = "、".join(fmt_dur(x["work_min"]) for x in r["recent_weeks"]) or "—"
    dota = "—"
    if r["last_week"]:
        dota = {True: "是", False: "否", None: "未知"}[r["last_week"]["dota_over"]]
    rules = "、".join("第%d条" % n for n, _ in r["rules"]) or "无"
    verdict = {"yes": "🛑 该请假", "no": "✅ 不该",
               "cooldown": "🧊 冷却中", "exhausted": "⛔ 配额用尽"}[r["verdict"]]
    return [r["today"].isoformat(), verdict, rules, weeks, dota,
            "%d天" % r["streak"], "%d/%d" % (r["used"], r["quota_max"])]


# ------------------------------------------------------------------ 入口

def main():
    p = argparse.ArgumentParser(description="请假触发判定器")
    p.add_argument("--status", action="store_true", help="只看配额/基线/冷却，不判定")
    p.add_argument("--take", metavar="YYYY-MM-DD", help="记录一天请假日已支取")
    p.add_argument("--undo", metavar="YYYY-MM-DD", help="撤销误记的支取")
    p.add_argument("--rule", default="手动", help="配合 --take：触发条件编号")
    p.add_argument("--note", default="", help="配合 --take：备注")
    p.add_argument("--json", action="store_true", help="判定结果输出 JSON")
    p.add_argument("--no-log", action="store_true", help="不写入判定历史")
    p.add_argument("--today", metavar="YYYY-MM-DD", help="覆盖今天日期（测试用）")
    p.add_argument("--work-file", default=WORK_FILE)
    p.add_argument("--dota-file", default=DOTA_FILE)
    p.add_argument("--ledger", default=LEDGER_FILE)
    args = p.parse_args()

    today = parse_date(args.today) if args.today else dt.date.today()
    state = load_ledger(args.ledger)

    # --- 记录 / 撤销支取 ---------------------------------------------------
    if args.take:
        d = parse_date(args.take)
        if not d:
            raise SystemExit("日期格式不对：%s" % args.take)
        if any(t["date"] == d for t in state["takes"]):
            print("已经记过 %s 了，没有重复写入。" % d.isoformat())
            return 0
        q = quarter_label(d)
        used = len([t for t in state["takes"] if t["quarter"] == q])
        state["takes"].append({
            "date": d, "quarter": q,
            "rule": args.rule if str(args.rule).startswith("第") else "第%s条" % args.rule,
            "recorded": today.isoformat(), "note": args.note,
        })
        state["takes"].sort(key=lambda t: t["date"])
        if state["baseline_min"] is None:
            state["baseline_min"] = SEED_BASELINE_MIN
            state["baseline_date"] = today
        save_ledger(args.ledger, state)
        print("已记录请假日 %s（%s，本季度第 %d 天）" % (d.isoformat(), q, used + 1))
        if used + 1 > QUARTER_QUOTA_MAX:
            print("⚠️  本季度已支取 %d 天，超过上限 %d 天。" % (used + 1, QUARTER_QUOTA_MAX))
        return 0

    if args.undo:
        d = parse_date(args.undo)
        before = len(state["takes"])
        state["takes"] = [t for t in state["takes"] if t["date"] != d]
        if len(state["takes"]) == before:
            print("台账里没有 %s 这条记录。" % args.undo)
            return 1
        save_ledger(args.ledger, state)
        print("已撤销 %s 的支取记录。" % d.isoformat())
        return 0

    # --- 判定 --------------------------------------------------------------
    weeks, active_days = load_work(args.work_file)
    dota = load_dota(args.dota_file)
    baseline_note = recompute_baseline(weeks, state, today)
    r = evaluate(today, weeks, active_days, dota, state)

    if args.status:
        r["verdict"] = "no" if not r["rules"] else r["verdict"]
        print(render(r, baseline_note))
        save_ledger(args.ledger, state)      # 只落基线，不写判定历史
        return 0

    if not args.no_log:
        state["log"].append(log_row(r))
    save_ledger(args.ledger, state)

    if args.json:
        out = dict(r)
        out["today"] = r["today"].isoformat()
        out["quarter_range"] = [r["quarter_range"][0].isoformat(),
                                r["quarter_range"][1].isoformat()]
        out["last_take"] = r["last_take"].isoformat() if r["last_take"] else None
        out["rules"] = [{"no": n, "desc": d} for n, d in r["rules"]]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render(r, baseline_note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
