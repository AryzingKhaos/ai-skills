#!/usr/bin/env bash
# 执行 `bash ./sync.sh` 就能同步

set -e

SRC="$HOME/code/ai-skills/skills"
CLAUDE="$HOME/.claude/skills"
CODEX="$HOME/.codex/skills"

echo "Syncing skills..."

mkdir -p "$CLAUDE"
mkdir -p "$CODEX"

# Claude
rsync -av \
  "$SRC/" \
  "$CLAUDE/"

# Codex (增量同步，保留 .system)
rsync -av \
  --exclude ".system" \
  "$SRC/" \
  "$CODEX/"

echo "Done."