#!/usr/bin/env bash
# Start (or reuse) this skill's isolated Chrome and print its CDP URL.
# Own user-data-dir => no SingletonLock clash with the user's daily Chrome, login persists across runs.
# --remote-debugging-port=0 => Chrome picks a free port and writes it to DevToolsActivePort.
set -euo pipefail
WORK="${BAIDU_SAVE_WORK:-$HOME/workspace/aiDownload/baidu-share-save}"
USERDATA="$WORK/chrome-userdata"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
mkdir -p "$USERDATA"

alive() {
  [ -f "$USERDATA/DevToolsActivePort" ] || return 1
  local port
  port=$(head -1 "$USERDATA/DevToolsActivePort")
  curl -s --max-time 2 "http://127.0.0.1:$port/json/version" >/dev/null || return 1
  echo "http://127.0.0.1:$port"
}

if url=$(alive); then
  # macOS keeps Chrome running after the last window is closed; with zero pages
  # connectOverCDP fails ("Browser context management is not supported"), so reopen one.
  if ! curl -s --max-time 2 "$url/json/list" | grep -q '"type": "page"'; then
    curl -s --max-time 5 -X PUT "$url/json/new?https://pan.baidu.com/disk/main" >/dev/null
    sleep 1
  fi
  echo "REUSE $url"; exit 0
fi
rm -f "$USERDATA/DevToolsActivePort"
nohup "$CHROME" --user-data-dir="$USERDATA" --remote-debugging-port=0 \
  --no-first-run --no-default-browser-check --new-window "https://pan.baidu.com/disk/main" \
  >/dev/null 2>&1 &
for _ in $(seq 1 40); do
  sleep 0.5
  if url=$(alive); then echo "STARTED $url"; exit 0; fi
done
echo "FAILED: Chrome did not expose a CDP port" >&2
exit 1
