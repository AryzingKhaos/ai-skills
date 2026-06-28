#!/usr/bin/env python3
"""
build_digest.py —— 把采集到的窗口 JSON + 全文 JSON 合并、按口径B过滤、去重双胞胎，
产出「最旧在前」的编号 digest（翻译/归纳的稿源）。

用法：
  python3 build_digest.py \
      --windows serenity_win_1.json serenity_win_2.json ... \
      --full serenity_full.json [serenity_full2.json ...] \
      --since 2026-01-01 --until 2026-01-31 \
      --min-len 260 \
      --out digest.txt \
      --handle aleabitoreddit

输入 JSON 形态：
  - window JSON：collect_window.js 落盘的数组，元素 {id, dt, txt, folded, len}
  - full JSON：backfill_batch.js 落盘的对象 { id: 全文string }（'__ERR__' 表示该条抓取失败）

输出：digest.txt，每条形如
  ### [N] YYYY-MM-DD HH:MM
  URL: https://x.com/<handle>/status/<id>
  <全文（无全文则用截断文本兜底）>
  <空行>
"""
import argparse, json, re, os, sys


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 读取 {path} 失败: {e}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", nargs="+", required=True, help="collect_window.js 落盘的窗口 JSON(可多个)")
    ap.add_argument("--full", nargs="*", default=[], help="backfill_batch.js 落盘的全文 JSON(可多个，后者覆盖前者)")
    ap.add_argument("--since", default="0000-00-00", help="起始日(含) YYYY-MM-DD")
    ap.add_argument("--until", default="9999-99-99", help="结束日(含) YYYY-MM-DD")
    ap.add_argument("--min-len", type=int, default=260, help="口径B 正文长度阈值(默认260)")
    ap.add_argument("--out", required=True, help="输出 digest 路径")
    ap.add_argument("--handle", default="aleabitoreddit", help="账号 handle(拼 URL 用)")
    args = ap.parse_args()

    # 1) 合并所有窗口，按 id 去重（保留正文更长者）
    by_id = {}
    for p in args.windows:
        data = load_json(p) or []
        for x in data:
            i = x.get("id")
            if not i:
                continue
            if i not in by_id or len(x.get("txt", "")) > len(by_id[i].get("txt", "")):
                by_id[i] = x
    print(f"合并窗口后唯一推文: {len(by_id)}")

    # 2) 合并全文映射
    full = {}
    for p in args.full:
        d = load_json(p) or {}
        full.update(d)

    # 3) 贴全文（无有效全文则用截断文本兜底）
    rows = []
    for x in by_id.values():
        ft = full.get(x["id"])
        x["fulltext"] = ft if (ft and ft != "__ERR__" and len(ft) >= len(x.get("txt", ""))) else x.get("txt", "")
        rows.append(x)

    # 4) 日期过滤
    def d10(x):
        return (x.get("dt") or "")[:10]
    rows = [x for x in rows if args.since <= d10(x) <= args.until]

    # 5) 口径B：folded 或 全文≥min_len
    rows = [x for x in rows if x.get("folded") or len(x["fulltext"]) >= args.min_len]

    # 6) 去重双胞胎：正文归一化(压空白+小写)取前100字，同组保留最长
    def norm(s):
        return re.sub(r"\s+", " ", s).strip().lower()[:100]
    seen = {}
    for x in rows:
        k = norm(x["fulltext"])
        if k not in seen or len(x["fulltext"]) > len(seen[k]["fulltext"]):
            seen[k] = x
    rows = list(seen.values())

    # 7) 最旧在前排序
    rows.sort(key=lambda x: x.get("dt") or "")

    # 8) 写 digest
    with open(args.out, "w", encoding="utf-8") as f:
        for i, x in enumerate(rows):
            dt = (x.get("dt") or "").replace("T", " ")[:16]
            f.write(f"### [{i}] {dt}\nURL: https://x.com/{args.handle}/status/{x['id']}\n{x['fulltext']}\n\n")

    # 9) 汇总
    from collections import Counter
    by_day = dict(sorted(Counter(d10(x) for x in rows).items()))
    short_folded = sum(1 for x in rows if x.get("folded") and len(x["fulltext"]) < 200)
    print(f"口径B digest 条数: {len(rows)}")
    print(f"按日分布: {by_day}")
    if short_folded:
        print(f"[warn] 仍偏短的 folded 条目(可能回填失败): {short_folded} —— 建议重试这些 id 的 backfill")
    print(f"已写出: {args.out} ({os.path.getsize(args.out)} bytes)")


if __name__ == "__main__":
    main()
