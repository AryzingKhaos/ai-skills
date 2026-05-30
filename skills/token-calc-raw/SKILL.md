---
name: token-calc-raw
description: "直接从本机 ~/.claude/projects/**/*.jsonl 聚合 Claude Code token 用量，不依赖 ccusage / npx 外部工具。支持自定义时间窗口（过去 N 天 / 起止日期），按模型 / 按天 / 按项目分组并折算 USD 成本。**只有显式调用才能使用此 skill**，禁止模糊匹配触发。显式调用形式：`/token-calc-raw [参数]`、'用 token-calc-raw 算 X 天用量'、'调用 token-calc-raw'。"
---

# token-calc-raw

直接从本机 `~/.claude/projects/**/*.jsonl` 会话存档聚合 Claude Code 的 token 使用与 USD 成本。**不调用 ccusage / npx 等外部工具**——本机 jsonl 里 `message.usage` 字段已经是原始数据。

## 触发约束（硬约束）

**只在用户显式调用时触发**，仅以下两种形态算显式调用：

1. 斜杠命令：`/token-calc-raw [天数 | 起止日期]`
2. 用户消息中明确出现 skill 名 **`token-calc-raw`**（如"用 token-calc-raw 算""调用 token-calc-raw 看下 7 天"）

❌ **禁止模糊匹配触发**：用户说"统计 token 用量""算下过去 7 天""帮我看 Claude Code 用量""不要跑 ccusage 自己算"等**不点名 skill** 的请求，**不要**自动启动本 skill；按普通对话处理（可以手写脚本跑，但不要走 skill 流程）。

如果用户希望同时统计 Codex 用量，**走 `token-calc` skill**（ccusage 路线），本 skill 只覆盖 Claude Code。

## 输入参数

从用户消息中解析时间窗口。**优先用 `SINCE`（起始日期）这种"绝对边界"，而不是"过去 N 天"，避免今天还没过完导致最末一天偏低**。

- "过去 7 天" / "最近 7 天" → `SINCE = today - 6 days`（含今天共 7 天）
- "过去 30 天" → 同理
- "5/19 到现在" → `SINCE = 2026-05-19`
- "5/19 ~ 5/25" → `SINCE = 2026-05-19`, `UNTIL = 2026-05-25`（默认 UNTIL = today）

today 必须从 system 提供的 currentDate 取，不要用 `date` 命令（可能时区错位）。

## 执行步骤

1. 解析时间窗口 → 算出 `SINCE`（和可选的 `UNTIL`，ISO `YYYY-MM-DD` 字符串）
2. 把下面的脚本写到 `/tmp/usage_count.py`（已存在则用 Edit 改 `SINCE` / `UNTIL`，不要重写）
3. 跑 `python3 /tmp/usage_count.py`
4. 把脚本输出按下面"输出格式"整理给用户

## 输出格式

按以下顺序输出 markdown 表格 + 简短点评：

1. **总览**（总花费 / 原始 token 总数 / 按 input 单价折算等效 / 消息数 / 扫描文件数）
2. **按模型**（每个 model 一行：input / output / cache_wr / cache_rd / msgs / cost）
3. **按天**（每天 raw_tokens / cost / msgs，标出峰值与低谷）
4. **Top 8 项目**（按花费排序）
5. **点评**：与"上一周期同长度窗口"对比（如有数据），指出涨跌大的项目；标注金额都是 list price 估算，订阅 plan 不按 token 计费

涉及 subagent 子目录（如 `subagents`）要单独标注"跨项目"。

## 严格禁止

- 跑 `npx -y ccusage` / `npx -y @ccusage/codex` 等外部工具
- 用 `find -maxdepth N` 限深（subagent 子会话可能在更深层，会漏算）
- 用 `xargs cat` 拼所有 jsonl 后再 jq（实测会因为部分文件没换行结尾导致丢数据）
- 把 token 按 number 浮点累加（用 int / BigInt）
- 把 cache_read tokens 按 output 价格折算（误差可达 50 倍）

## 价格表（USD per MTok，list price）

脚本里已内置。如 Anthropic 调价或新增模型，直接改 `PRICES` 字典。

| 模型族 | input | output | cache_write (5min) | cache_read |
|--------|------:|-------:|-------------------:|-----------:|
| opus | $15 | $75 | $18.75 | $1.5 |
| sonnet | $3 | $15 | $3.75 | $0.30 |
| haiku | $1 | $5 | $1.25 | $0.10 |

匹配规则：model 字符串里包含 `opus` / `sonnet` / `haiku` 子串即归类（覆盖 `claude-opus-4-7` / `claude-opus-4-8` / `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` 等所有命名形态）。

## 脚本：/tmp/usage_count.py

```python
#!/usr/bin/env python3
"""
Aggregate Claude Code token usage from ~/.claude/projects/**/*.jsonl
since SINCE (inclusive). Output by model / by day / by project.
"""
import json, os, glob, sys
from collections import defaultdict

SINCE = "2026-05-22"      # YYYY-MM-DD inclusive
UNTIL = None              # None = today; otherwise YYYY-MM-DD inclusive
ROOT  = os.path.expanduser("~/.claude/projects")

# USD per MTok (Anthropic list price)
PRICES = {
    "opus":   (15.0, 75.0, 18.75, 1.5),
    "haiku":  (1.0,  5.0,  1.25,  0.10),
    "sonnet": (3.0,  15.0, 3.75,  0.30),
}

def price_for(m):
    for k, v in PRICES.items():
        if k in m:
            return v
    return (0, 0, 0, 0)

by_model = defaultdict(lambda: [0,0,0,0,0,0.0])  # in, out, cc, cr, msgs, cost
by_date  = defaultdict(lambda: [0,0.0,0])        # raw, cost, msgs
by_proj  = defaultdict(lambda: [0,0.0,0])        # raw, cost, msgs

files = glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True)
print(f"scanning {len(files)} jsonl files", file=sys.stderr)

for f in files:
    proj = os.path.basename(os.path.dirname(f))
    try:
        with open(f, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"assistant"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                ts = o.get("timestamp") or ""
                d = ts[:10]
                if not d or d < SINCE:
                    continue
                if UNTIL and d > UNTIL:
                    continue
                msg = o.get("message") or {}
                u = msg.get("usage") or {}
                m = msg.get("model") or "unknown"
                it  = u.get("input_tokens", 0) or 0
                ot  = u.get("output_tokens", 0) or 0
                cct = u.get("cache_creation_input_tokens", 0) or 0
                crt = u.get("cache_read_input_tokens", 0) or 0
                pi, po, pcc, pcr = price_for(m)
                cost = it*pi/1e6 + ot*po/1e6 + cct*pcc/1e6 + crt*pcr/1e6
                raw = it + ot + cct + crt
                by_model[m][0] += it; by_model[m][1] += ot
                by_model[m][2] += cct; by_model[m][3] += crt
                by_model[m][4] += 1;  by_model[m][5] += cost
                by_date[d][0] += raw; by_date[d][1] += cost; by_date[d][2] += 1
                by_proj[proj][0] += raw; by_proj[proj][1] += cost; by_proj[proj][2] += 1
    except Exception as e:
        print(f"err {f}: {e}", file=sys.stderr)

window = f"{SINCE} ~ {UNTIL or 'today'}"

print(f"\n=== 按模型（{window}）===")
print(f"{'model':35s} {'input':>13s} {'output':>13s} {'cache_wr':>14s} {'cache_rd':>14s} {'msgs':>7s} {'cost$':>10s}")
ti=to=tcc=tcr=tm=0; tco=0.0
for m, v in sorted(by_model.items(), key=lambda x: -x[1][5]):
    ti+=v[0]; to+=v[1]; tcc+=v[2]; tcr+=v[3]; tm+=v[4]; tco+=v[5]
    print(f"{m:35s} {v[0]:13d} {v[1]:13d} {v[2]:14d} {v[3]:14d} {v[4]:7d} {v[5]:10.2f}")
print(f"{'TOTAL':35s} {ti:13d} {to:13d} {tcc:14d} {tcr:14d} {tm:7d} {tco:10.2f}")
raw_total = ti+to+tcc+tcr
print(f"\nRAW TOKENS (4 类相加): {raw_total:,}  (≈ {raw_total/1e8:.2f} 亿)")
print(f"按 input 单价 $15/MTok 折算等效: {tco/15*1e6:,.0f}  (≈ {tco/15*1e6/1e8:.2f} 亿)")

print(f"\n=== 按天 ===")
print(f"{'date(UTC)':12s} {'raw_tokens':>14s} {'cost$':>10s} {'msgs':>6s}")
for d in sorted(by_date.keys()):
    v = by_date[d]
    print(f"{d:12s} {v[0]:14d} {v[1]:10.2f} {v[2]:6d}")

print(f"\n=== Top 8 项目（按金额）===")
print(f"{'cost$':>10s}  {'project':50s} {'raw_tokens':>14s} {'msgs':>6s}")
for p, v in sorted(by_proj.items(), key=lambda x: -x[1][1])[:8]:
    print(f"{v[1]:10.2f}  {p:50s} {v[0]:14d} {v[2]:6d}")
```

## 实现注意

- 用 Python `glob.glob(..., recursive=True)`，不要用 shell `find -maxdepth`
- 行级提前过滤：`if '"assistant"' not in line: continue`——比每行都 `json.loads` 快 10 倍以上
- `errors="ignore"` 跳过偶发编码错误的字节
- timestamp 是 ISO UTC 字符串，直接字符串比较 `>= SINCE` 即可，不必 parse 成 datetime
- subagent 的 jsonl 在更深目录，所以一定要 recursive
- 价格匹配用 `if k in m`：`claude-opus-4-7` / `claude-opus-4-8` / 未来的 `opus-5` 全自动归入 opus 族
