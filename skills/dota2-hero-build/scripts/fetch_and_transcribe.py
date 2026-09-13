#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对**已通过画面核验**的视频做全量处理：下载 → 抽帧 → 转写 → 立即删掉大文件。

只在闸门一（元数据）+ 闸门二（画面）都过了之后才跑这一步——这是整条链路里
唯一昂贵的环节（下载 40–130MB + whisper 数分钟），所以绝不能对无关视频跑。

whisper 用 initial_prompt 做术语偏置。⚠️ initial_prompt 上限约 224 token，
所以只塞英雄名 + 高频装备黑话，不要把 544 件装备全喂进去。

产物（<outdir>/<BVID>/）:
    segments.txt        带时间轴逐句（保留，是最终引用锚点）
    raw_transcript.txt  整段原文（保留）
    frames/             少量关键帧（保留，用于核验与报告）
    video.mp4 / audio.wav  处理完默认删除（--keep-media 保留）

用法:
    python3 fetch_and_transcribe.py --bvid BV1pYtH6MEje --hero 虚空假面 --outdir out
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dota_meta  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# whisper initial_prompt 有 ~224 token 上限，这里只放最高频的黑话
CORE_SLANG = ["黑皇杖", "BKB", "疯狂面具", "疯脸", "闪烁匕首", "跳刀", "战斧", "狂战斧",
              "代达罗斯之殇", "大炮", "蝴蝶", "阿哈利姆神杖", "A杖", "魔晶", "斯嘉蒂之眼",
              "冰眼", "雷神之锤", "电锤", "深渊之刃", "散失之刃", "撒旦之邪力", "分身斧",
              "银月之晶", "神灭斩", "洞察烟斗", "相位鞋", "动力鞋", "远行鞋", "刷新球",
              "正补", "反补", "补刀", "推高地", "开雾", "对线期", "真空期", "一号位", "天梯"]


def run(cmd, timeout=1800, quiet=False):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 and not quiet:
        print((p.stderr or "")[-600:], file=sys.stderr)
    return p.returncode


def build_prompt(hero_cn):
    return ("以下是中文 DOTA2 解说，请输出规范的简体中文并加标点。可能出现的词：%s、%s。"
            % (hero_cn, "、".join(CORE_SLANG)))


def transcribe(wav, outdir, hero_cn, model="large-v3-turbo"):
    import whisper
    m = whisper.load_model(model)
    r = m.transcribe(wav, language="zh", task="transcribe", fp16=False,
                     initial_prompt=build_prompt(hero_cn), verbose=False,
                     condition_on_previous_text=False)
    with open(os.path.join(outdir, "raw_transcript.txt"), "w", encoding="utf-8") as f:
        f.write((r.get("text") or "").strip() + "\n")
    with open(os.path.join(outdir, "segments.txt"), "w", encoding="utf-8") as f:
        for s in r.get("segments", []):
            st, en = int(s["start"]), int(s["end"])
            f.write("[%02d:%02d:%02d -> %02d:%02d:%02d] %s\n"
                    % (st // 3600, st // 60 % 60, st % 60,
                       en // 3600, en // 60 % 60, en % 60, s["text"].strip()))
    return len((r.get("text") or "")), len(r.get("segments", []))



# whisper 在静音/纯 BGM 段的已知幻觉串（实测反复出现）
HALLUCINATIONS = ["优优独播剧场", "YoYo Television", "请不吝点赞", "字幕由", "Amara.org",
                  "订阅我的频道", "明镜与点点栏目"]


def quality_check(d, nchar, secs):
    """判断这份转写能不能拿来做出装分析。

    **字数不够用——密度才是判据。** 实测：
      有解说的视频 200+ 字/分钟；纯集锦/无解说的只有 ~60 字/分钟，
      但 43 分钟 × 60 = 2551 字，照样过得了『字数 > 800』的检查。
    """
    mins = max(secs / 60.0, 0.1) if secs else 0
    seg = os.path.join(d, "segments.txt")
    hall = 0
    if os.path.exists(seg):
        with open(seg, encoding="utf-8") as f:
            for line in f:
                if any(h in line for h in HALLUCINATIONS):
                    hall += 1
    bad = []
    if nchar < 800:
        bad.append("字数仅 %d（<800）" % nchar)
    if mins and nchar / mins < 100:
        bad.append("密度仅 %.0f 字/分钟（<100，有解说的视频通常 200+）" % (nchar / mins))
    if hall:
        bad.append("检出 %d 行 whisper 静音幻觉" % hall)

    if bad:
        print("⚠️  转写质量不合格：%s" % "；".join(bad), flush=True)
        print("    → 多半是集锦/无解说视频，**不要拿它做出装分析**，回到闸门二换候选。",
              flush=True)
    else:
        print("    质量检查通过：%.0f 字/分钟，无幻觉行。" % (nchar / mins if mins else 0),
              flush=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bvid", required=True)
    p.add_argument("--hero", required=True, help="英雄官方中文名（用于术语偏置）")
    p.add_argument("--outdir", required=True, help="工件根目录，会建 <outdir>/<BVID>/")
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--frame-interval", type=int, default=90, help="关键帧采样间隔秒")
    p.add_argument("--browser", default="chrome")
    p.add_argument("--keep-media", action="store_true", help="保留 mp4/wav（默认删）")
    p.add_argument("--retries", type=int, default=3)
    a = p.parse_args()

    r = dota_meta.resolve_hero(a.hero)
    hero_cn = r["hero"]["name_loc"] if r["hero"] else a.hero

    d = os.path.join(a.outdir, a.bvid)
    os.makedirs(os.path.join(d, "frames"), exist_ok=True)
    mp4, wav = os.path.join(d, "video.mp4"), os.path.join(d, "audio.wav")

    if not os.path.exists(mp4):
        for i in range(1, a.retries + 1):
            print("[%s] 下载 尝试 %d ..." % (a.bvid, i), flush=True)
            rc = run('yt-dlp --no-warnings --user-agent "%s" '
                     '--add-header "Referer:https://www.bilibili.com/" '
                     '--cookies-from-browser %s --sleep-requests 3 '
                     '--retries 10 --fragment-retries 10 -S "res:480" '
                     '--merge-output-format mp4 -o "%s" '
                     '"https://www.bilibili.com/video/%s"'
                     % (UA, a.browser, os.path.join(d, "video.%(ext)s"), a.bvid),
                     quiet=(i < a.retries))
            if os.path.exists(mp4):
                break
            run("sleep %d" % (i * 15), quiet=True)
        if not os.path.exists(mp4):
            print("❌ %s 下载失败" % a.bvid); return 1

    if not os.path.exists(wav):
        run('ffmpeg -nostdin -loglevel error -y -i "%s" -ar 16000 -ac 1 "%s"' % (mp4, wav))
    if not os.path.exists(wav):
        print("❌ 抽音频失败"); return 1

    run('ffmpeg -nostdin -loglevel error -y -i "%s" -vf "fps=1/%d,scale=960:-1" '
        '-vsync vfr "%s/frames/f_%%03d.jpg"' % (mp4, a.frame_interval, d), quiet=True)
    nframe = len([x for x in os.listdir(os.path.join(d, "frames")) if x.endswith(".jpg")])

    mp4_secs = 0
    try:
        import subprocess as _sp
        mp4_secs = float(_sp.run('ffprobe -v error -show_entries format=duration '
                                 '-of csv=p=0 "%s"' % mp4, shell=True,
                                 capture_output=True, text=True).stdout.strip() or 0)
    except Exception:
        pass

    print("[%s] 转写中（%s）..." % (a.bvid, a.model), flush=True)
    nchar, nseg = transcribe(wav, d, hero_cn, a.model)

    if not a.keep_media:
        for f in (mp4, wav):
            if os.path.exists(f):
                os.remove(f)
        print("[%s] 已删除 video.mp4 / audio.wav（中间产物）" % a.bvid, flush=True)

    print("[%s] 完成：转写 %d 字 / %d 句，关键帧 %d 张 → %s"
          % (a.bvid, nchar, nseg, nframe, d), flush=True)
    quality_check(d, nchar, mp4_secs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
