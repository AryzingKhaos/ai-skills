---
name: x-scraper
description: "用 Playwright（用户已登录的浏览器会话）绕过 X/Twitter 限流，抓取指定账号的历史推文（含被折叠的长帖全文），产出可翻译/归纳的 digest。核心手法：日期窗口搜索绕开深滚墙、多语言折叠检测、status 详情页分批回填全文+冷却、双胞胎去重。触发场景：'抓取 @某人 的历史推特'、'爬一下这个 X 账号的所有观点'、'把某人的推文整理成时间线'、'归档某个 twitter 账号'、'绕过推特限流爬推文'、'/x-scraper @handle'、'获取某账号关于 xxx 的全部发帖'。不适用：抓单条推文（直接开 status 页即可，无需本流程）；非 X 平台。"
---

# x-scraper · 用 Playwright 抓取 X/Twitter 账号历史推文

把一个 X 账号的历史发帖（尤其**被折叠的长 thesis 全文**）系统抓下来，去重、过滤、整理成「最旧在前」的编号 digest，作为后续**翻译 / 方法论归纳 / 时间线整理**的稿源。

> 这套流程是从一个真实项目里打磨出来的（把某 AI/半导体分析师 5 个月、上千条推文抓全并逐条翻译）。**最值钱的不是代码，是下面这些踩坑经验**——尤其「两套独立限流」和「多语言折叠检测」，不知道就会白忙。

## 前置：浏览器与工具

- 依赖 **Playwright MCP**（用户已登录的 X 会话）。这些工具默认是**延迟加载**的，先用 ToolSearch 拉起：
  ```
  ToolSearch("select:mcp__playwright__browser_navigate,mcp__playwright__browser_run_code_unsafe,mcp__playwright__browser_evaluate")
  ```
- 三件套：`browser_navigate`（开页）、`browser_run_code_unsafe`（跑 Playwright JS，能 `filename` 从文件加载）、`browser_evaluate`（在页面求值，`filename` 参数可把返回值落盘成 JSON）。
- 本 skill 的脚本目录：`/Users/aaron/code/ai-skills/skills/x-scraper/scripts/`
  - `collect_window.js`：滚动当前搜索页、采集推文
  - `backfill_batch.js`：逐条开详情页补全长帖全文（分批+冷却）
  - `build_digest.py`：合并/过滤/去重/出 digest

## ⚠️ 三条必须先知道的铁律（踩过的坑）

1. **别深滚个人主页时间线**。`x.com/<handle>` 往下滚 **~9 天就被硬限流**（console 报错、停止加载）。✅ 正解：用**高级搜索按日期窗口**抓，每窗约 8 天，绕开深滚墙：
   ```
   https://x.com/search?q=from%3A<handle>%20since%3AYYYY-MM-DD%20until%3AYYYY-MM-DD&f=live
   ```
   （`%3A`=`:`，`f=live` 取最新流。回溯越久就把窗口往前平移。）

2. **折叠检测必须多语言**。X 的 UI 语言会随机切到中/日/韩，长帖的「Show more」会变成「显示更多 / 顯示更多 / もっと見る / 더 보기」。若只认英文，`folded` 会**恒为 0**、漏掉所有需要回填全文的长帖，最后只能靠 `len>=260` 兜底（不可靠）。脚本里的 `FOLD` 数组已含五语，**别删**。

3. **两套限流是相互独立的**——这是整个流程能跑通的关键：
   - **搜索端口（search-live）**：连开 ~4-5 个窗 + 大量滚动后进入「Something went wrong. Try reloading.」硬限流，分钟级到一小时恢复。
   - **详情页端口（status）**：连抓 ~70-100 条后 fail 率飙升，靠**冷却**解决。
   - **二者独立**：搜索被限时，详情页照样能抓全文；反之亦然。所以策略是搜索窗之间留间隔、回填可在搜索被限时照常进行。

## 标准流水线（4 步）

### ① 采集每个日期窗（搜索端口）

逐窗执行：`browser_navigate` 打开窗口搜索 URL → 跑 `collect_window.js` 滚动采集 → `browser_evaluate` 落盘。

```
browser_navigate("https://x.com/search?q=from%3A<handle>%20since%3A2026-01-19%20until%3A2026-01-28&f=live")
browser_run_code_unsafe({ filename: ".../scripts/collect_window.js" })          // 返回 {total, folded, hv, dates}
browser_evaluate({ function: "() => Object.values(window.__t)", filename: "win_0119.json" })
```

- **先改 `collect_window.js` 顶部的 `HANDLE`** 为目标账号（小写、无 @）。
- 看返回的 `dates` 确认窗口确实覆盖到目标日期（窗口的 `until` 是开区间，实际只回溯到某天就够；下一窗 `until` 接上即可，**让相邻窗各重叠一天**保证不漏）。
- 一个月通常切 4 个窗（each ~8 天）。连采 4-5 窗后若下一窗报限流，等几分钟或先去做②（详情页独立）。
- 落盘文件名按窗区分（`win_0119.json` / `win_0112.json` …）。

### ② 合并去重 + 过滤口径 → 得到待回填 id 列表

把所有窗 JSON 合并、按日期裁剪、过滤「口径B（folded 或 正文≥260）」、列出 `folded` 的 id（这些才需要回填全文）。这步可以直接用 `build_digest.py` 的逻辑，或先用一小段 Python 抽 folded id。**口径 B = 只要原创长 thesis/方法论帖**（正文≥260 或原折叠），跳过短回复/纯里程碑/双胞胎——按需调 `--min-len`。

### ③ 回填长帖全文（详情页端口）

主页面先停在一条 status 作**稳定基座**（回填中途主页面绝不能再被 navigate，否则 `window.__ids/__cur/__sf` 丢失），注入 id 列表，反复跑 `backfill_batch.js` 直到 `cur == 总数`，最后落盘全文映射。

```
browser_navigate("https://x.com/<handle>/status/<任一folded id>")     // 稳定基座
browser_evaluate({ function: "() => { window.__ids=['id1','id2',...]; window.__cur=0; window.__sf={}; return window.__ids.length; }" })
# 反复跑（每批24条、批间冷却28s、单条间隔4.5s），直到 cur 到顶：
browser_run_code_unsafe({ filename: ".../scripts/backfill_batch.js" })   // 返回 {ok, err, cur, totalSf}
...
browser_evaluate({ function: "() => window.__sf", filename: "full.json" })   // { id: 全文 }
```

- **先改 `backfill_batch.js` 顶部的 `HANDLE`**（拼 URL 用）。
- 实测每批 24 条、冷却 28-30s、间隔 4.5s，可稳过几百条、几乎 0 错。某批 `err` 偏高就调小 `BATCH`、调长 `COOLDOWN`，最后单独重试 miss 的 id。
- 注入 id 字符串很长时，直接把列表内联进 `browser_evaluate` 的 function 字符串即可（用 Python 先 `json.dumps` 出列表文本）。

### ④ 出 digest（翻译/归纳的稿源）

```
python3 .../scripts/build_digest.py \
    --windows win_0119.json win_0112.json win_0105.json win_1229.json \
    --full full.json \
    --since 2026-01-01 --until 2026-01-31 \
    --handle <handle> \
    --out digest_202601.txt
```

产出「最旧在前」的编号 digest（`### [N] 时间 / URL / 全文`）。它会：合并窗口去重 → 贴全文（无则截断兜底）→ 日期裁剪 → 口径B过滤 → **双胞胎去重**（正文归一化取前100字、同组留最长）→ 排序。脚本末尾会打印按日分布和「仍偏短的 folded」告警（提示哪些回填失败、需重试）。

## run_code_unsafe 沙箱注意

- **禁止** `require` / `import` / `fs` / `setTimeout`。等待用 `page.waitForTimeout(ms)`；开新标签用 `page.context().newPage()`。
- `globalThis` **不**跨调用持久；但**主页面的 `window`**（在不导航时）跨调用持久——`window.__t/__ids/__cur/__sf` 全靠这点。
- `browser_evaluate` 的 `filename` 参数能把返回值**落盘成 JSON**（采集结果、全文映射都靠它导出，不占对话上下文）。
- 时间线/搜索流的正文**截断在 ~280 字符**；要全文必须走③（详情页）。

## DOM 选择器（X 改版时在这里改）

- 推文卡片：`article[data-testid="tweet"]`
- 正文：`[data-testid="tweetText"]`
- 作者：`[data-testid="User-Name"]`（用它过滤掉混入的「他回复的别人原帖」）
- 时间：`time[datetime]`（ISO，取 UTC）
- 链接里的 id：`a[href*="/status/"]` → `/status/(\d+)/`

## 体量与节奏经验

- 单月去重后通常几百条实质长帖，约一半 folded 需取全文。回填几百条 ≈ 十几个 `backfill_batch.js` 批次（含冷却），耐心慢抓最稳。
- 翻译/归纳才是真正的 token 大头；采集本身上下文占用很低（结果都落盘）。
- 付费订阅帖只能见开头，标注「付费·未解锁」即可。

## 合规与边界

- 仅抓**公开**推文，用的是用户自己已登录的会话。
- 若把抓来的观点做成对外产物，**务必标注**：内容系第三方公开言论的客观归纳、不代表立场、不构成投资建议；若作者本人持有并喊单相关标的，注明利益冲突与「未独立核实」。
- 不要用本流程做高频骚扰式抓取、绕过付费墙、或抓非公开内容。
