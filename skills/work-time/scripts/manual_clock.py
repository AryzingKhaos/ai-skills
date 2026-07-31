#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work-time 手动打卡子命令：start / end / endTime。

定位：记录**自动推算（对话记录）抓不到的工作**——看书、开会、线下写代码、纯思考。
与自动推算互补，各记各的、不重叠。手动段是**精确起止**，不加准备/阅读补偿。

进行中状态存 STATE 文件（同目录 .state.json）。end/endTime 时配对写入台账的
「手动打卡明细」表，然后清空状态。

用法：
    python3 manual_clock.py start ["本次大致工作内容"]
    python3 manual_clock.py end   ["可选内容；若给了则覆盖 start 的内容"]
    python3 manual_clock.py endTime "HH:MM" 或 "MM-DD HH:MM"   # 补忘记写的结束时间
    python3 manual_clock.py status                             # 看当前是否有进行中的打卡

时间用脚本执行的系统时间（本地时区）。
"""
import os
import sys
import json
import datetime

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".manual_clock_state.json")
LEDGER = "/Users/aaron/workspace/个人/生活/工作时长.md"
MANUAL_HEADER = "## 手动打卡明细（自动抓不到的工作：看书/开会/线下/纯思考；精确起止，不加补偿）"
MANUAL_COLS = "| 日期 | 起 | 止 | 时长 | 内容 | 备注 |"
MANUAL_SEP = "|---|---|---|---|---|---|"
WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def now_local():
    return datetime.datetime.now().astimezone()


def fmt_date_wd(dt):
    return "%s 周%s" % (dt.strftime("%Y-%m-%d"), WEEKDAY_CN[dt.weekday()])


def fmt_dur(sec):
    sec = int(round(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    if h:
        return "%d 小时 %d 分钟" % (h, m)
    return "%d 分钟" % m


def load_state():
    if not os.path.exists(STATE):
        return None
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(d):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def clear_state():
    if os.path.exists(STATE):
        os.remove(STATE)


def parse_end_time(s, start_dt):
    """把 'HH:MM' 或 'MM-DD HH:MM' 解析为本地 aware datetime。
    只给 HH:MM 时默认与 start 同一天；若解析出的时间早于 start，则视为跨到次日。"""
    s = s.strip()
    tz = start_dt.tzinfo
    try:
        if " " in s:  # MM-DD HH:MM
            md, hm = s.split()
            mo, da = [int(x) for x in md.split("-")]
            hh, mm = [int(x) for x in hm.split(":")]
            dt = start_dt.replace(month=mo, day=da, hour=hh, minute=mm, second=0, microsecond=0)
        else:  # HH:MM
            hh, mm = [int(x) for x in s.split(":")]
            dt = start_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt < start_dt:  # 结束早于开始 → 跨天
                dt += datetime.timedelta(days=1)
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt
    except Exception:
        return None


def do_start(content):
    st = load_state()
    if st:  # 已有未结束的段 → 提示用户决定（不擅自处理）
        s_start = st.get("start_h", "?")
        s_date = st.get("date", "?")
        print("⚠️ 已有一段未结束的打卡：%s %s 开始「%s」。" % (s_date, s_start, st.get("content", "")))
        print("请先用 `end`（现在结束）或 `endTime HH:MM`（补结束时间）结束它，再开新段。")
        print("（如确实要丢弃这段旧打卡，删除文件：%s）" % STATE)
        sys.exit(1)
    now = now_local()
    save_state({
        "start_iso": now.isoformat(),
        "date": fmt_date_wd(now),
        "start_h": now.strftime("%H:%M"),
        "content": content or "",
    })
    print("▶️ 已开始打卡：%s %s" % (fmt_date_wd(now), now.strftime("%H:%M")))
    if content:
        print("   内容：%s" % content)
    print("   结束时用 `end`（可带内容覆盖）或 `endTime HH:MM`（补时间）。")


def _write_ledger(start_dt, end_dt, content, note=""):
    dur = end_dt - start_dt
    row = "| %s | %s | %s | %s | %s | %s |" % (
        fmt_date_wd(start_dt), start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M"),
        fmt_dur(dur.total_seconds()), content or "", note)
    if not os.path.exists(LEDGER):
        text = "# 工作时长台账\n\n"
    else:
        with open(LEDGER, encoding="utf-8") as f:
            text = f.read()
    if MANUAL_HEADER in text:
        # 在该表最后一行后追加：定位到 header，找到其表格块末尾插入
        idx = text.index(MANUAL_HEADER)
        # 从 header 之后找下一个 "## " 或文件末尾
        after = text[idx + len(MANUAL_HEADER):]
        nxt = after.find("\n## ")
        block_end = (idx + len(MANUAL_HEADER) + nxt) if nxt != -1 else len(text)
        block = text[:block_end].rstrip()
        text = block + "\n" + row + "\n" + text[block_end:]
    else:
        # 新建表，放文件末尾
        text = text.rstrip() + "\n\n" + MANUAL_HEADER + "\n\n" + MANUAL_COLS + "\n" + MANUAL_SEP + "\n" + row + "\n"
    with open(LEDGER, "w", encoding="utf-8") as f:
        f.write(text)
    return dur


def _finish(end_dt, content_override):
    st = load_state()
    if not st:
        print("⚠️ 当前没有进行中的打卡（没有 start）。如需补记，先 `start` 再结束，或手动编辑台账。")
        sys.exit(1)
    start_dt = datetime.datetime.fromisoformat(st["start_iso"])
    if end_dt <= start_dt:
        print("⚠️ 结束时间 %s 不晚于开始时间 %s，请检查。" % (end_dt.strftime("%H:%M"), start_dt.strftime("%H:%M")))
        sys.exit(1)
    # end 若带内容则覆盖 start 内容（用户规则）
    content = content_override if content_override else st.get("content", "")
    dur = _write_ledger(start_dt, end_dt, content)
    clear_state()
    print("⏹️ 已结束打卡并记入台账：")
    print("   %s  %s → %s  用时 %s" % (
        fmt_date_wd(start_dt), start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M"), fmt_dur(dur.total_seconds())))
    if content:
        print("   内容：%s" % content)
    if content_override and st.get("content"):
        print("   （已用 end 的内容覆盖 start 的「%s」）" % st["content"])


def do_end(content_override):
    _finish(now_local(), content_override)


def do_end_time(timestr):
    st = load_state()
    if not st:
        print("⚠️ 当前没有进行中的打卡（没有 start），endTime 无处可补。")
        sys.exit(1)
    start_dt = datetime.datetime.fromisoformat(st["start_iso"])
    end_dt = parse_end_time(timestr, start_dt)
    if not end_dt:
        print("⚠️ 结束时间格式无法解析：%r。用 HH:MM 或 MM-DD HH:MM。" % timestr)
        sys.exit(1)
    _finish(end_dt, None)


def do_status():
    st = load_state()
    if not st:
        print("当前没有进行中的打卡。")
        return
    start_dt = datetime.datetime.fromisoformat(st["start_iso"])
    elapsed = now_local() - start_dt
    print("⏱️ 进行中：%s %s 开始，已 %s。内容：%s" % (
        st.get("date"), st.get("start_h"), fmt_dur(elapsed.total_seconds()), st.get("content") or "(未填)"))


def main():
    args = sys.argv[1:]
    if not args:
        print("用法：start [内容] | end [内容] | endTime HH:MM | status")
        sys.exit(1)
    cmd = args[0]
    rest = args[1:]
    arg = " ".join(rest).strip() if rest else ""
    if cmd == "start":
        do_start(arg)
    elif cmd == "end":
        do_end(arg)
    elif cmd in ("endTime", "endtime"):
        if not arg:
            print("⚠️ endTime 需要跟结束时间，如 `endTime 22:30`。")
            sys.exit(1)
        do_end_time(arg)
    elif cmd == "status":
        do_status()
    else:
        print("未知子命令：%s（支持 start / end / endTime / status）" % cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
