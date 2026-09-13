#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸门二：只下几秒片段抽帧，供模型看图核验「这到底是不是 Dota2 当前版本」。

为什么必须有这一关（spike 2026-09-04 实测）：
  - DotA1（魔兽争霸3）内容的标题、标签、简介、**连转写稿都完全干净**——
    转写里同样是 BKB / 疯脸 / 跳刀，纯文本管线会安静地把 6.83 的出装写进 7.41e 分析。
  - 只有画面能区分：DotA1 是 WC3 界面 + 右上角 `DotA6.8x` 水印 + 英雄叫「暗惧者」。
  - 顺带还能从天赋树/HUD 独立确认客户端版本，不必相信发布日期。

成本：`yt-dlp --download-sections` 每段 ~1.2MB / ~17s，比整片（40–130MB）便宜约 60 倍。
抽完帧立刻删掉片段 mp4，只留 jpg。

用法:
    python3 probe_frames.py --cand cand.json --outdir probe --top 8
    python3 probe_frames.py --bvid BV1pYtH6MEje --duration 1189 --outdir probe
"""

import argparse
import json
import os
import subprocess
import sys
import time

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def run(cmd, timeout=300):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def probe_one(bvid, duration_s, outdir, browser="chrome", points=(0.35, 0.65),
              seg=8, retries=3):
    """下 len(points) 个 seg 秒片段，各抽 1 帧。返回帧路径列表。"""
    os.makedirs(outdir, exist_ok=True)
    frames = []
    dur = max(int(duration_s or 0), 60)
    for i, frac in enumerate(points):
        at = int(dur * frac)
        tmp = os.path.join(outdir, "_seg_%s_%d.mp4" % (bvid, i))
        jpg = os.path.join(outdir, "%s_at%04ds.jpg" % (bvid, at))
        if os.path.exists(jpg):
            frames.append(jpg)
            continue
        cmd = ('yt-dlp --no-warnings --user-agent "%s" '
               '--add-header "Referer:https://www.bilibili.com/" '
               '--cookies-from-browser %s -S "res:480" '
               '--download-sections "*%d-%d" --force-keyframes-at-cuts '
               '-o "%s" "https://www.bilibili.com/video/%s"'
               % (UA, browser, at, at + seg, tmp, bvid))
        rc = 1
        for attempt in range(1, retries + 1):
            rc, out = run(cmd)
            if rc == 0 and os.path.exists(tmp):
                break
            if "412" in out:
                print("  [%s] @%ds 412 限流，退避 %ds 后重试(%d/%d)"
                      % (bvid, at, attempt * 15, attempt, retries), file=sys.stderr, flush=True)
            time.sleep(attempt * 15)
        if rc != 0 or not os.path.exists(tmp):
            print("  [%s] 片段 @%ds 下载失败（已重试 %d 次）"
                  % (bvid, at, retries), file=sys.stderr, flush=True)
            continue
        rc2, _ = run('ffmpeg -nostdin -loglevel error -y -i "%s" '
                     '-vf "scale=960:-1" -frames:v 1 "%s"' % (tmp, jpg))
        os.remove(tmp)                       # 片段用完即删，只留 jpg
        if rc2 == 0 and os.path.exists(jpg):
            frames.append(jpg)
    return frames


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cand", help="find_videos.py 产出的候选 JSON")
    p.add_argument("--bvid", help="单个 BV 号（与 --cand 二选一）")
    p.add_argument("--duration", type=int, default=600, help="配合 --bvid：视频秒数")
    p.add_argument("--outdir", required=True)
    p.add_argument("--top", type=int, default=8, help="核验候选前 N 个")
    p.add_argument("--browser", default="chrome")
    p.add_argument("--seg", type=int, default=8, help="每个片段秒数")
    p.add_argument("--retries", type=int, default=3, help="412 限流重试次数")
    p.add_argument("--sleep", type=float, default=5, help="候选之间间隔秒（防限流）")
    a = p.parse_args()

    targets = []
    if a.bvid:
        targets = [{"bvid": a.bvid, "duration_s": a.duration, "title": "", "author": ""}]
    elif a.cand:
        with open(a.cand, encoding="utf-8") as f:
            targets = json.load(f)["candidates"][: a.top]
    else:
        p.error("需要 --cand 或 --bvid")

    os.makedirs(a.outdir, exist_ok=True)
    report = []
    for i, c in enumerate(targets, 1):
        print("[%d/%d] %s  %s" % (i, len(targets), c["bvid"], (c.get("title") or "")[:40]))
        fr = probe_one(c["bvid"], c.get("duration_s"), a.outdir, a.browser,
                       seg=a.seg, retries=a.retries)
        time.sleep(a.sleep)
        for f in fr:
            print("      %s" % f)
        report.append({"bvid": c["bvid"], "title": c.get("title"),
                       "author": c.get("author"), "patch": c.get("patch"),
                       "frames": fr})

    out = os.path.join(a.outdir, "probe_index.json")
    merged = {}
    if os.path.exists(out):                    # 合并：单个 --bvid 跑不该抹掉已有记录
        try:
            with open(out, encoding="utf-8") as f:
                for r_ in json.load(f):
                    merged[r_["bvid"]] = r_
        except Exception:
            pass
    for r_ in report:
        merged[r_["bvid"]] = r_
    report = list(merged.values())
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    total = sum(len(r["frames"]) for r in report)
    print("\n共 %d 个候选、%d 帧 → %s" % (len(report), total, out))
    print("下一步：用 Read 逐帧看图核验（是不是 Dota2 现代界面／版本对不对／有没有解说画面）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
