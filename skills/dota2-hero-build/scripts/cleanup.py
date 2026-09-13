#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收尾：删掉无关中间产物，只留成果。

删：video.mp4 / audio.wav / whisper.log / 片段临时文件 /
    **被淘汰候选的核验帧**（保留通过者的少量帧）
留：报告 md、segments.txt、raw_transcript.txt、通过者的关键帧、候选清单 JSON

用法:
    python3 cleanup.py --dir out --keep BV1pYtH6MEje --keep BV1vh8A6CEnC
    python3 cleanup.py --dir out --keep BV1pYtH6MEje --max-frames 6 --dry-run
"""

import argparse
import os
import shutil
import sys

DROP_NAMES = {"video.mp4", "audio.wav", "whisper.log"}
DROP_EXT = (".part", ".ytdl", ".m4a", ".webm", ".mkv")


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, u)
        n /= 1024.0
    return "%.1fTB" % n


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True, help="工件根目录")
    p.add_argument("--keep", action="append", default=[], help="保留的 BVID，可多次")
    p.add_argument("--max-frames", type=int, default=8, help="每个保留视频最多留几张帧")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    root = os.path.abspath(a.dir)
    if not os.path.isdir(root):
        print("目录不存在：%s" % root); return 1
    keep = set(a.keep)
    freed, removed = 0, []

    def rm(path):
        nonlocal freed
        sz = 0
        if os.path.isdir(path):
            for dp, _, fs in os.walk(path):
                for f in fs:
                    try:
                        sz += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
        else:
            try:
                sz = os.path.getsize(path)
            except OSError:
                pass
        freed += sz
        removed.append((path, sz))
        if not a.dry_run:
            shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)

    # 1) 大文件与临时文件
    for dp, dns, fs in os.walk(root):
        for f in fs:
            if f in DROP_NAMES or f.endswith(DROP_EXT) or f.startswith("_seg_"):
                rm(os.path.join(dp, f))

    # 2) 未保留的候选目录（含其核验帧）整个删掉
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path) or not name.startswith("BV"):
            continue
        if name not in keep:
            rm(path)

    # 3) 核验帧（无论在 probe/ 还是散落在任意层级）：只留 keep 的
    import re as _re
    pat = _re.compile(r"^(BV[0-9A-Za-z]+)_at\d+s\.jpg$")
    for dp, _, fs in os.walk(root):
        for f in fs:
            m = pat.match(f)
            if m and m.group(1) not in keep:
                rm(os.path.join(dp, f))

    # 4) 保留视频的关键帧限量
    for k in keep:
        fdir = os.path.join(root, k, "frames")
        if not os.path.isdir(fdir):
            continue
        jpgs = sorted(x for x in os.listdir(fdir) if x.endswith(".jpg"))
        if len(jpgs) > a.max_frames:
            step = len(jpgs) / float(a.max_frames)
            keep_idx = {int(i * step) for i in range(a.max_frames)}
            for i, j in enumerate(jpgs):
                if i not in keep_idx:
                    rm(os.path.join(fdir, j))

    tag = "[dry-run] 将删除" if a.dry_run else "已删除"
    print("%s %d 项，释放 %s" % (tag, len(removed), human(freed)))
    for path, sz in removed[:25]:
        print("  %-9s %s" % (human(sz), os.path.relpath(path, root)))
    if len(removed) > 25:
        print("  ... 另有 %d 项" % (len(removed) - 25))
    print("\n保留：%s" % (", ".join(sorted(keep)) or "（无 --keep，只清了大文件）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
