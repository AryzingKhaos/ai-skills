#!/usr/bin/env python3
"""english-talk 对话记录器。

从 stdin 读一个 JSON 对象，把这一轮对话追加到当天的场景日志，
并把生词 upsert 进该场景的词汇本（按词去重、累加出现次数）。

用法：
    python3 talk_log.py --scene dota2 <<'JSON'
    {
      "user": "I want push mid lane",
      "reply": "Let's push mid...",
      "corrections": [
        {"level": "major", "wrong": "I want push", "right": "I want to push",
         "note": "want 后面接动词要用不定式 to push"}
      ],
      "vocab": [
        {"word": "siege", "pos": "v./n.", "meaning": "围攻，强攻高地",
         "example": "We should siege their base before the buyback timer resets."},
        {"word": "Roshan", "tag": "专名·Dota2", "pos": "n.", "meaning": "肉山",
         "example": "We took Roshan while they were respawning."}
      ]
    }
    JSON

其它可选顶层字段：
    "summary": "本次小结正文"     -> 追加一个「## 本次小结」区块（收尾用）
    "note":    "任意备注"         -> 追加一行斜体备注

参数：
    --scene   场景名（目录名），必填
    --root    English 根目录，默认 /Users/aaron/workspace/个人/生活/English
    --date    日期 YYYY-MM-DD，默认今天（用于补记）
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

DEFAULT_ROOT = "/Users/aaron/workspace/个人/生活/English"
VOCAB_HEADER = "| 词 | 类别 | 词性 | 中文释义 | 例句 | 首见 | 次数 |"
VOCAB_SEP = "|---|---|---|---|---|---|---|"
DEFAULT_TAG = "通用"


def cell(text):
    """把任意文本压成一个安全的表格单元格。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text.replace("|", r"\|")


def uncell(text):
    return text.replace(r"\|", "|").strip()


def read(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return ""


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 当天日志

def append_turn(day_path, scene, date, payload):
    body = read(day_path)
    if not body:
        body = f"# {scene} · {date}\n\n> 由 /english-talk 自动记录。\n"

    blocks = []

    if payload.get("user") or payload.get("reply"):
        turn_no = len(re.findall(r"^### ", body, flags=re.M)) + 1
        stamp = dt.datetime.now().strftime("%H:%M")
        blocks.append(f"\n### Turn {turn_no} · {stamp}\n")

        if payload.get("user"):
            blocks.append(f"\n**Me:** {payload['user'].strip()}\n")
        if payload.get("reply"):
            blocks.append(f"\n**Reply:** {payload['reply'].strip()}\n")

        corrections = payload.get("corrections") or []
        if payload.get("user"):  # 开场白这类没有用户发言的轮次不写矫正块
            blocks.append("\n**矫正**\n\n")
            if not corrections:
                blocks.append("- ✅ 本轮没有需要纠正的地方。\n")
        for c in (corrections if payload.get("user") else []):
            if isinstance(c, str):
                blocks.append(f"- {c.strip()}\n")
                continue
            mark = "🔴" if str(c.get("level", "")).lower() == "major" else "🟡"
            wrong = str(c.get("wrong", "")).strip()
            right = str(c.get("right", "")).strip()
            note = str(c.get("note", "")).strip()
            line = f"- {mark} "
            if wrong and right:
                line += f"~~{wrong}~~ → **{right}**"
            elif right:
                line += f"**{right}**"
            if note:
                line += f" — {note}" if (wrong or right) else note
            blocks.append(line.rstrip() + "\n")

        vocab = payload.get("vocab") or []
        if vocab:
            blocks.append("\n**生词**\n\n")
            for v in vocab:
                if isinstance(v, str):
                    blocks.append(f"- {v.strip()}\n")
                    continue
                line = f"- **{str(v.get('word', '')).strip()}**"
                if v.get("tag"):
                    line += f" `{str(v['tag']).strip()}`"
                if v.get("phonetic"):
                    line += f" /{str(v['phonetic']).strip('/ ')}/"
                if v.get("pos"):
                    line += f" *{str(v['pos']).strip()}*"
                if v.get("meaning"):
                    line += f" — {str(v['meaning']).strip()}"
                blocks.append(line + "\n")
                if v.get("example"):
                    blocks.append(f"  - e.g. {str(v['example']).strip()}\n")

    if payload.get("note"):
        blocks.append(f"\n*{str(payload['note']).strip()}*\n")

    if payload.get("summary"):
        stamp = dt.datetime.now().strftime("%H:%M")
        blocks.append(f"\n---\n\n## 本次小结 · {stamp}\n\n{payload['summary'].strip()}\n")

    if not blocks:
        return False

    if not body.endswith("\n"):
        body += "\n"
    write(day_path, body + "".join(blocks))
    return True


# ---------------------------------------------------------------- 词汇本

def load_vocab(path):
    """返回 (顺序无关的 dict[key] = row, 已存在文件?)"""
    rows = {}
    body = read(path)
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---") or line == VOCAB_HEADER:
            continue
        parts = [uncell(p) for p in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(parts) < 6 or parts[0] in ("词", ""):
            continue
        if len(parts) >= 7:
            word, tag, pos, meaning, example, first_seen, count = parts[:7]
        else:  # 兼容没有「类别」列的旧词汇本
            word, pos, meaning, example, first_seen, count = parts[:6]
            tag = DEFAULT_TAG
        try:
            count = int(count)
        except ValueError:
            count = 1
        rows[word.strip("* ").lower()] = {
            "word": word, "tag": tag or DEFAULT_TAG, "pos": pos, "meaning": meaning,
            "example": example, "first_seen": first_seen, "count": count,
        }
    return rows


def upsert_vocab(path, scene, vocab, date):
    if not vocab:
        return 0, 0
    rows = load_vocab(path)
    added = updated = 0
    for v in vocab:
        if isinstance(v, str):
            v = {"word": v}
        word = str(v.get("word", "")).strip()
        if not word:
            continue
        key = word.lower()
        if key in rows:
            row = rows[key]
            row["count"] += 1
            # 后补上原来缺失的字段，但不覆盖已有内容
            for field in ("pos", "meaning", "example"):
                if not row[field] and v.get(field):
                    row[field] = cell(v[field])
            if v.get("tag") and row["tag"] in ("", DEFAULT_TAG):
                row["tag"] = cell(v["tag"])
            updated += 1
        else:
            rows[key] = {
                "word": word,
                "tag": cell(v.get("tag", "")) or DEFAULT_TAG,
                "pos": cell(v.get("pos", "")),
                "meaning": cell(v.get("meaning", "")),
                "example": cell(v.get("example", "")),
                "first_seen": date,
                "count": 1,
            }
            added += 1

    lines = [
        f"# {scene} · 词汇本",
        "",
        "> 由 /english-talk 自动维护：按字母序排列，同一个词再次出现时「次数」累加。\n> 「类别」为「通用」的是可迁移的英语词汇，标「专名」的是场景专有名词（英雄名、装备名、技术术语等）。",
        "",
        VOCAB_HEADER,
        VOCAB_SEP,
    ]
    for key in sorted(rows):
        r = rows[key]
        lines.append(
            f"| {cell(r['word'])} | {cell(r['tag'])} | {cell(r['pos'])} | "
            f"{cell(r['meaning'])} | {cell(r['example'])} | {cell(r['first_seen'])} | {r['count']} |"
        )
    write(path, "\n".join(lines) + "\n")
    return added, updated


def main():
    ap = argparse.ArgumentParser(description="english-talk 对话记录器")
    ap.add_argument("--scene", required=True, help="场景名，即 English 下的子目录名")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="English 根目录")
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args()

    raw = sys.stdin.read().strip()
    if not raw:
        print("没有从 stdin 收到 JSON，什么都没写。", file=sys.stderr)
        return 1
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败：{e}", file=sys.stderr)
        return 1

    scene = args.scene.strip()
    date = args.date or dt.date.today().isoformat()
    scene_dir = os.path.join(args.root, scene)
    day_path = os.path.join(scene_dir, f"{date}.md")
    vocab_path = os.path.join(scene_dir, "词汇.md")

    wrote = append_turn(day_path, scene, date, payload)
    added, updated = upsert_vocab(vocab_path, scene, payload.get("vocab") or [], date)

    parts = []
    if wrote:
        parts.append(f"已写入 {day_path}")
    if added or updated:
        parts.append(f"词汇本 +{added} 新词 / {updated} 复现 → {vocab_path}")
    print("；".join(parts) if parts else "无内容写入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
