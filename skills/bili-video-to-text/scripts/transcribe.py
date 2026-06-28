#!/usr/bin/env python3
"""用 openai-whisper 把音频转写成文字。

用法:
    python3 transcribe.py <音频路径> <输出目录> [模型=medium] [语言=zh]

为什么是脚本而非命令行：openai-whisper 的 `whisper` CLI 常不在 PATH 里，但 python
包本身可以 import，所以这里直接用 Python API 跑，最稳。

为什么放后台跑：CPU 上用 medium/large 转写十几分钟的音频通常要十几到几十分钟，会超过
单次命令的超时限制。调用方应把本脚本放后台执行（run_in_background）。

产物（写入 <输出目录>）:
    raw_transcript.txt  —— 整段原始转写文本（whisper 的 result["text"]）
    segments.txt        —— 带时间轴的逐句分段，供整理时参考定位
"""
import sys
import time

import whisper  # openai-whisper

audio = sys.argv[1]
outdir = sys.argv[2].rstrip("/")
model_name = sys.argv[3] if len(sys.argv) > 3 else "medium"
language = sys.argv[4] if len(sys.argv) > 4 else "zh"

# initial_prompt 偏置：促使模型输出规范的简体中文 + 标点。whisper 中文有时会吐繁体，
# 这个提示能缓解；即便仍有繁体，后续「整理成文稿」步骤也会统一为简体。
prompt = "以下是中文普通话内容，请输出规范的简体中文，并加上正确的标点。"
if language != "zh":
    prompt = None

t0 = time.time()
print("loading %s model ..." % model_name, flush=True)
model = whisper.load_model(model_name)  # 首次会自动下载模型权重（需联网）
print("model loaded in %.0fs, transcribing (lang=%s) ..." % (time.time() - t0, language), flush=True)

result = model.transcribe(
    audio,
    language=language,
    task="transcribe",
    fp16=False,            # CPU 必须 False，否则告警/变慢
    initial_prompt=prompt,
    verbose=False,
)

with open(outdir + "/raw_transcript.txt", "w", encoding="utf-8") as f:
    f.write(result.get("text", "").strip() + "\n")

with open(outdir + "/segments.txt", "w", encoding="utf-8") as f:
    for seg in result["segments"]:
        f.write("[%.1f-%.1f] %s\n" % (seg["start"], seg["end"], seg["text"].strip()))

print("DONE segments=%d elapsed=%.0fs" % (len(result["segments"]), time.time() - t0), flush=True)
