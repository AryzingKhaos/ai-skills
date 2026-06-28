---
name: token-calc
description: "用于统计本机内的 claude code 和 codex 的 token 使用量情况，只有显式调用才能使用此skill"
---
帮我统计最近 4 周的 Claude Code 和 Codex token 使用情况。  

**数据获取：**  
1. Claude：运行 `npx -y ccusage@latest daily --json`，需手动将日期**按（上周五～本周四）**聚合为4周数据
2. Codex：运行 `npx -y @ccusage/codex daily --json`，该工具没有 weekly 命令，需手动将日期**按（上周五～本周四）**聚合为4周数据

**字段映射（两者结构不同）：**  
- Claude：  
- Raw I/O = inputTokens + outputTokens  
- Cache Write = cacheCreationTokens  
- Cache Read = cacheReadTokens  
- Codex：  
- Raw I/O = (inputTokens - cachedInputTokens) + outputTokens  
- Cache Write = 无此字段，填 "-"  
- Cache Read = cachedInputTokens  
  
**输出要求：**  
- 分别输出 Claude 表格 和 Codex 表格，单位 M（百万）  
- 若某工具无数据则跳过对应表格  
- 每张表格底部附 4 周平均行  
- 最后输出三行摘要：  
- Claude: <名字> | 近4周 | RawI/O均 X M | Cache均 X M | Total均 X M/周 | $X/周  
- Codex: <名字> | 近4周 | RawI/O均 X M | Cache均 X M | Total均 X M/周 | $X/周  
- 合计: <名字> | 近4周 | Claude $X/周 + Codex $X/周 = $X/周  
  
**文件输出：**  
将两张表格和摘要保存为 CSV 到桌面，  
文件名：claude&codex-token-usage-<名字>-<今天日期YYYYMMDD>.csv