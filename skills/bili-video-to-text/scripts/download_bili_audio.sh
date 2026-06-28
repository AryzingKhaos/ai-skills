#!/usr/bin/env bash
# 下载 B 站（或其它 yt-dlp 支持站点）视频音轨，抽取为 16kHz 单声道 wav（whisper 友好）。
# 用法: download_bili_audio.sh <视频URL> <输出目录> [cookies浏览器]
#
# 为什么需要浏览器 cookie：B 站对没有有效会话的请求会返回 HTTP 412 (Precondition
# Failed)，光加 UA/Referer、甚至手动塞 buvid3 都不够，必须带「浏览器里的 B 站登录
# cookie」。yt-dlp 的 --cookies-from-browser 正是干这个的。脚本会依次尝试常见浏览器。
#
# 成功后向 stdout 打印（供调用方解析）:
#   TITLE=<视频标题>
#   DURATION_SEC=<秒数>
#   AUDIO=<wav 绝对路径>
set -uo pipefail

URL="${1:?用法: download_bili_audio.sh <URL> <输出目录> [浏览器]}"
OUTDIR="${2:?需要输出目录}"
FORCE_BROWSER="${3:-}"
mkdir -p "$OUTDIR"

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
COMMON=(--no-warnings --user-agent "$UA" --add-header "Referer:https://www.bilibili.com/")

if [ -n "$FORCE_BROWSER" ]; then
  BROWSERS=("$FORCE_BROWSER")
else
  BROWSERS=(chrome safari edge firefox brave chromium)
fi

# 1) 先用 --simulate 探测哪个浏览器的 cookie 能过 412，并顺便拿到标题/时长
WORKING_BROWSER=""
TITLE=""; DURATION=""
for b in "${BROWSERS[@]}"; do
  echo ">> 探测 $b cookie ..." >&2
  meta=$(yt-dlp "${COMMON[@]}" --cookies-from-browser "$b" --simulate \
          --print "%(title)s|||%(duration)s" "$URL" 2>/dev/null) || continue
  if [ -n "$meta" ]; then
    WORKING_BROWSER="$b"
    TITLE="${meta%%|||*}"
    DURATION="${meta##*|||}"
    echo ">> $b 可用，标题: $TITLE" >&2
    break
  fi
done

if [ -z "$WORKING_BROWSER" ]; then
  echo "!! 所有浏览器 cookie 都过不了。请确认：" >&2
  echo "   1) 你在某个浏览器里登录过 bilibili.com；" >&2
  echo "   2) 用 --cookies-from-browser 指定该浏览器（脚本第3个参数）。" >&2
  exit 1
fi

# 2) 用可用的浏览器 cookie 正式下载音轨并抽成 16k 单声道 wav
echo ">> 用 $WORKING_BROWSER 下载音轨 ..." >&2
yt-dlp "${COMMON[@]}" --cookies-from-browser "$WORKING_BROWSER" \
  -f bestaudio -x --audio-format wav \
  --postprocessor-args "-ar 16000 -ac 1" \
  -o "$OUTDIR/audio.%(ext)s" "$URL" >&2
rc=$?
if [ $rc -ne 0 ] || [ ! -f "$OUTDIR/audio.wav" ]; then
  echo "!! 下载/抽取失败 (rc=$rc)" >&2
  exit 1
fi

echo "TITLE=$TITLE"
echo "DURATION_SEC=${DURATION%.*}"
echo "AUDIO=$OUTDIR/audio.wav"
