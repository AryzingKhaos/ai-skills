#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work-time - estimate real "working with AI" hours from local Claude Code + Codex logs.

Logic (user-defined):
- Collect every conversation event timestamp
  (Claude: ~/.claude/projects/**/*.jsonl ; Codex: ~/.codex/sessions/**/*.jsonl).
- Sort all timestamps; if the gap between adjacent events <= GAP_MIN (default 30) min,
  they belong to the same continuous work segment; otherwise start a new segment.
- Each segment: add PREP_MIN (default 5) min before its start, READ_MIN (default 10) min after its end.
- Sum = estimated AI-assisted work time.

Purpose: OBSERVE, not judge. Report how long / which segments / which days only.
No target, no "enough or not" verdict.

Usage:
    python3 work_time.py [--days N] [--gap-min 30] [--prep-min 5] [--read-min 10]
    no --days -> current natural week (this Monday 00:00 -> now)
    --days N  -> past N days (until yesterday 24:00)
"""
import os
import json
import glob
import argparse
import datetime

CLAUDE_GLOB = os.path.expanduser("~/.claude/projects/**/*.jsonl")
CODEX_GLOB = os.path.expanduser("~/.codex/sessions/**/*.jsonl")

# 本职工作项目（tronlink 等），在 planB 统计里排除。可用 --work-projects 覆盖。
WORK_PROJECTS = {"pro"}


def parse_ts(s):
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone()
    except Exception:
        return None


def project_of(path, source):
    """从 jsonl 路径推断项目名。
    Claude: ~/.claude/projects/<项目目录名>/xxx.jsonl -> 项目目录名（已是编码过的 cwd）。
    Codex:  ~/.codex/sessions/YYYY/MM/DD/rollout-...jsonl -> 无目录信息，用 codex 会话的 cwd（见下），
            拿不到就标 'codex'。"""
    if source == "claude":
        # 上一级目录名，如 -Users-aaron-code-ai-skills
        return os.path.basename(os.path.dirname(path))
    return None  # codex 的项目从行内 payload.cwd 取（见 collect）


def short_project(name):
    """把 -Users-aaron-code-ai-skills 或 /Users/aaron/code/ai-skills 简化成 ai-skills 这种末段名。"""
    if not name:
        return "未知"
    n = name.replace("\\", "/")
    # claude 目录名是把 / 换成 - 的编码，如 -Users-aaron-code-ai-skills
    if "/" not in n and n.startswith("-"):
        parts = [p for p in n.split("-") if p]
    else:
        parts = [p for p in n.split("/") if p]
    return parts[-1] if parts else name


def collect_events(globpat, source):
    """返回 [(dt, project_name), ...]。project_name 尽量取到项目末段名。"""
    out = []
    for path in glob.iglob(globpat, recursive=True):
        try:
            fh = open(path, encoding="utf-8", errors="ignore")
        except Exception:
            continue
        file_proj = project_of(path, source)
        codex_cwd = None
        with fh as f:
            for line in f:
                line = line.strip()
                if not line or line[0] != "{":
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                # codex: 从 session_meta 的 payload.cwd 取项目
                if source == "codex" and codex_cwd is None:
                    pl = d.get("payload")
                    if isinstance(pl, dict) and pl.get("cwd"):
                        codex_cwd = pl["cwd"]
                dt = parse_ts(d.get("timestamp") or d.get("ts"))
                if dt:
                    proj = file_proj if source == "claude" else (codex_cwd or "codex")
                    out.append((dt, short_project(proj)))
    return out


def fmt_dur(sec):
    sec = int(round(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    if h:
        return "%d 小时 %d 分钟" % (h, m)
    return "%d 分钟" % m


WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def fmt_date_wd(dt):
    """2026-07-20 周一"""
    return "%s 周%s" % (dt.strftime("%Y-%m-%d"), WEEKDAY_CN[dt.weekday()])


def main_projs(projlist):
    """段内涉及的所有项目，按事件数从多到少排序，用 '/' 连接。"""
    cnt = {}
    for p in projlist:
        cnt[p] = cnt.get(p, 0) + 1
    ordered = sorted(cnt, key=lambda k: -cnt[k])
    return "/".join(ordered)


def build(events, gap, prep, read):
    """把已排序的 events 切段并算时长。返回 (seg_rows, seg_details, total, by_day)。
    seg_rows: [(adj_start, adj_end, dur, projstr), ...]"""
    if not events:
        return [], [], datetime.timedelta(), {}
    segments = []
    seg_start = events[0][0]
    prev = events[0][0]
    seg_projs = [events[0][1]]
    for dt, proj in events[1:]:
        if dt - prev > gap:
            segments.append((seg_start, prev, seg_projs))
            seg_start = dt
            seg_projs = []
        seg_projs.append(proj)
        prev = dt
    segments.append((seg_start, prev, seg_projs))

    total = datetime.timedelta()
    by_day = {}
    seg_rows = []
    seg_details = []
    for first, last, projs in segments:
        adj_start = first - prep
        adj_end = last + read
        dur = adj_end - adj_start
        total += dur
        day = first.date().isoformat()
        by_day[day] = by_day.get(day, datetime.timedelta()) + dur
        pstr = main_projs(projs)
        seg_rows.append((adj_start, adj_end, dur, pstr))
        seg_details.append({
            "date": fmt_date_wd(adj_start),
            "start": adj_start.strftime("%H:%M"), "end": adj_end.strftime("%H:%M"),
            "human": fmt_dur(dur.total_seconds()), "seconds": int(dur.total_seconds()),
            "project": pstr,
        })
    return seg_rows, seg_details, total, by_day


def render_detail(lines, title, seg_rows):
    lines.append("## %s" % title)
    lines.append("| 段 | 日期 | 起(含准备) | 止(含阅读) | 时长 | 项目 |")
    lines.append("|---|---|---|---|---|---|")
    for i, (s, e, dur, proj) in enumerate(seg_rows, 1):
        lines.append("| %d | %s | %s | %s | %s | %s |"
                     % (i, fmt_date_wd(s), s.strftime("%H:%M"), e.strftime("%H:%M"),
                        fmt_dur(dur.total_seconds()), proj))
    lines.append("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--gap-min", type=int, default=30, dest="gap_min")
    ap.add_argument("--prep-min", type=int, default=5, dest="prep_min")
    ap.add_argument("--read-min", type=int, default=10, dest="read_min")
    ap.add_argument("--work-projects", default=None, dest="work_projects",
                    help="逗号分隔的本职工作项目名（在 planB 统计里排除）；默认 pro")
    a = ap.parse_args()

    work_projs = (set(p.strip() for p in a.work_projects.split(",") if p.strip())
                  if a.work_projects else set(WORK_PROJECTS))

    now = datetime.datetime.now().astimezone()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if a.days is None:
        win_start = today0 - datetime.timedelta(days=today0.weekday())
        win_end = now
        mode_label = "本周（自然周，本周一 00:00 -> 现在）"
        mode_key = "natural_week"
    else:
        win_end = today0
        win_start = today0 - datetime.timedelta(days=a.days)
        mode_label = "过去 %d 天（截止昨天 24:00）" % a.days
        mode_key = "last_%d_days" % a.days

    events = collect_events(CLAUDE_GLOB, "claude") + collect_events(CODEX_GLOB, "codex")
    events = sorted((e for e in events if win_start <= e[0] <= win_end), key=lambda x: x[0])

    lines = []
    lines.append("# work-time · 用 AI 工作时长 · %s" % mode_label)
    lines.append("")

    if not events:
        lines.append("窗口内没有找到 Claude / Codex 对话记录（这段时间没用 AI 工作，或在看书）。")
        lines.append("")
        lines.append("SIGNALS: " + json.dumps(
            {"mode": mode_key, "total_seconds": 0, "segments": 0, "days_worked": 0,
             "seg_details": [], "planb_seg_details": [], "planb_total_seconds": 0, "planb_segments": 0},
            ensure_ascii=False))
        print("\n".join(lines))
        return

    gap = datetime.timedelta(minutes=a.gap_min)
    prep = datetime.timedelta(minutes=a.prep_min)
    read = datetime.timedelta(minutes=a.read_min)

    # 全部
    seg_rows, seg_details, total, by_day = build(events, gap, prep, read)
    # planB：剔除"项目属于工作黑名单"的事件（该事件不参与切段/计时）
    pb_events = [(dt, proj) for dt, proj in events if proj not in work_projs]
    pb_rows, pb_details, pb_total, pb_by_day = build(pb_events, gap, prep, read)

    lines.append("> 口径：Claude+Codex 对话事件按时间排序，间隔 <=%d 分钟算连续段；每段前加 %d 分钟准备、后加 %d 分钟阅读。**观测用，不设目标、不评判。**"
                 % (a.gap_min, a.prep_min, a.read_min))
    lines.append("> planB 统计 = 排除本职工作项目（%s）后的事件重新切段。" % ("/".join(sorted(work_projs)) or "无"))
    lines.append("")
    lines.append("- 🧑‍💻 **AI 工作真实时长（全部）：%s**（%d 段，覆盖 %d 天）"
                 % (fmt_dur(total.total_seconds()), len(seg_rows), len(by_day)))
    lines.append("- 🅱️ **其中 planB（排除 %s）：%s**（%d 段，覆盖 %d 天）"
                 % ("/".join(sorted(work_projs)) or "无", fmt_dur(pb_total.total_seconds()), len(pb_rows), len(pb_by_day)))
    lines.append("")
    lines.append("## 按天（全部）")
    lines.append("| 日期 | 时长 |")
    lines.append("|---|---|")
    for d in sorted(by_day):
        d_dt = datetime.date.fromisoformat(d)
        lines.append("| %s 周%s | %s |" % (d, WEEKDAY_CN[d_dt.weekday()], fmt_dur(by_day[d].total_seconds())))
    lines.append("")
    render_detail(lines, "工作段明细（全部）", seg_rows)
    render_detail(lines, "planB 工作段明细（排除 %s）" % ("/".join(sorted(work_projs)) or "无"), pb_rows)

    lines.append("SIGNALS: " + json.dumps({
        "mode": mode_key,
        "window_local": [win_start.strftime("%Y-%m-%d %H:%M"), win_end.strftime("%Y-%m-%d %H:%M")],
        "work_projects": sorted(work_projs),
        "total_seconds": int(total.total_seconds()),
        "total_human": fmt_dur(total.total_seconds()),
        "segments": len(seg_rows),
        "days_worked": len(by_day),
        "planb_total_seconds": int(pb_total.total_seconds()),
        "planb_total_human": fmt_dur(pb_total.total_seconds()),
        "planb_segments": len(pb_rows),
        "planb_days_worked": len(pb_by_day),
        "gap_min": a.gap_min, "prep_min": a.prep_min, "read_min": a.read_min,
        "seg_details": seg_details,
        "planb_seg_details": pb_details,
    }, ensure_ascii=False))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
