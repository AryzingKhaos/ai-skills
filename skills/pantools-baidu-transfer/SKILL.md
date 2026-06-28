---
name: pantools-baidu-transfer
description: "把 PanTools 目录分享页（list.pantools.cn/u_xxx，可能带访问密码）里某个文件夹下的所有文件（含多层子文件夹），通过『分享→打开链接→提取文件→保存到网盘』批量转存到用户百度网盘的指定目录。用一个独立的、与用户日常 Chrome 隔离的浏览器实例（独立 user-data-dir + 专用远程调试端口），由 playwright-core 经 CDP 驱动；百度登录态由用户在该独立窗口里自行登录、随 profile 持久化。内置：递归遍历目录树+分页抓全量文件清单(manifest)、逐个转存(设置保存路径=指定目录→保存到网盘→校验『保存成功』)、断点续跑(progress.json 跳过已存)、限流自动暂停。**只有显式调用才能使用此 skill**，禁止模糊匹配触发。显式调用形式：'/pantools-baidu-transfer <分享链接> [密码] [目标网盘目录]'、'用 pantools-baidu-transfer 把这个 pantools 链接的文件转存到我的网盘 AI视频保存'、'调用 pantools-baidu-transfer 批量保存到网盘'。"
---

# PanTools 目录 → 百度网盘 批量转存

把一个 **PanTools 目录分享页**（`https://list.pantools.cn/u_xxxx`，常带访问密码）里某文件夹下、**含多层子文件夹**的全部文件，逐个 **转存到用户百度网盘的指定目录**。

整条链路：**启动隔离浏览器 + 用户登录百度 → 解锁列表 → 递归抓全量文件清单(manifest) → 逐个转存(设路径→保存到网盘→校验) → 进度落盘可续跑**。

> 本质是串行任务：**一个浏览器、一个网盘、有限流**，所以是顺序循环，不要并行化（多 agent 抢同一个 Chrome 标签会乱、百度也会风控）。

---

## 触发约束（硬约束）

**只在用户显式调用时触发**：`/pantools-baidu-transfer ...`、"用/调用 pantools-baidu-transfer ..."。用户只是贴个 pantools 链接、随口说"帮我存一下"不算。

## 前置 / 参数

- 需要 `node`（v18+）。脚本依赖 `playwright-core`（首次在 `scripts/` 里 `npm i playwright-core`，仅用于经 CDP 连接系统 Chrome，不下载浏览器）。
- 参数：**分享链接**（必填）、**访问密码**（如有）、**目标网盘目录名**（如 `AI视频保存`，需已存在于用户网盘；不存在则先让用户建或在选目录弹窗里『新建文件夹』）、**起始文件夹**（要遍历哪个顶层文件夹，如 `2026`）。

## 步骤

### 1. 启动隔离浏览器（关键：避开 profile 锁 + 端口冲突）

用**独立 user-data-dir**（与用户日常 Chrome 隔离，避免 `SingletonLock` 冲突）和**专用调试端口**启动系统 Chrome。先探测端口是否被占（用户的 Chrome 可能已占 9222），占用就换端口（9333/9344…）：

```bash
TMP="<某个工作目录>/playwright"; mkdir -p "$TMP/userdata"
PORT=9333  # 若被占改 9344 等
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$TMP/userdata" --remote-debugging-port=$PORT \
  --no-first-run --no-default-browser-check --new-window "https://pan.baidu.com/" &
# 校验：curl -s http://127.0.0.1:$PORT/json/version 应返回 Browser/webSocketDebuggerUrl
```

后续所有脚本用 `CDP_URL=http://127.0.0.1:$PORT` 环境变量连接。**绝不 kill 用户自己的 Chrome**；只 `pkill -f "userdata"`（匹配本临时目录）清理本 skill 自己起的实例。

### 2. 用户登录百度网盘

让用户在弹出的独立窗口里登录百度网盘（扫码/账号）。登录态随该独立 profile 持久化，重启该 profile 仍在。用 `node scripts/shot.js` 类截图确认已进入网盘文件页（非登录页）。

### 3. 打开并解锁 PanTools 列表

在该浏览器新开标签到分享链接（脚本会用已连接的 context）。若有访问密码：
```bash
CDP_URL=http://127.0.0.1:$PORT node scripts/unlock.js <密码>
```
注意：密码框 `input.password-input`，确认按钮 `#passwordConfirm`；**别误填到顶部搜索框** `input.search-input`（会过滤列表）——unlock.js 会先清空搜索框。

### 4. 递归抓全量文件清单 → manifest

```bash
CDP_URL=http://127.0.0.1:$PORT node scripts/enumerate.js "<起始文件夹,如 2026>" "$TMP/manifest.json"
```
DFS 遍历所有子文件夹、处理分页（每页 50），产出 `{count, files:[{path,folder,fsId,name}]}`。**先把总数报给用户**。

### 5. 逐个转存（可后台 + 断点续跑）

```bash
CDP_URL=http://127.0.0.1:$PORT node scripts/run-all.js "$TMP/manifest.json" "$TMP/progress.json" "<目标目录,如 AI视频保存>"
```
单文件流程（`process-file.js`）：列表页点该文件 `.file-actions` 的分享按钮 → `#share-modal` 里点「打开链接」（新开百度标签，提取码已在 URL）→ 「提取文件」→ 进文件页 → 若 `.save-path` ≠ `我的网盘/<目标>` 则点 `.bottom-save-path-icon` 选目标目录「确定」→ 用页面内 `a[node-type="bottomShareSave"]` 点「保存到网盘」(被 canvas 遮挡，需 in-page click) → 校验弹窗 `#emptyDialogId` 含「保存成功 已保存至【…】」→ 关标签 → 下一个。

- 文件多（几百个、每个约 15–20s，可能 1–2 小时）：用后台运行，`progress.json` 每存一个就写，**重跑自动跳过已存**。
- 百度记住上次保存目录，故第 2 个起路径多半已默认对，脚本两种情况都兼容。
- 遇到限流（「请稍后重试/频繁」）自动暂停 60s；失败项记进 progress 不卡整批。

### 6. 校验与收尾

跑完读 `progress.json` 的 `stats`（saved/failed/skipped），把 failed 列表报给用户、可对这些 fsId 重跑。可选：导航网盘到目标目录截图核对数量。

## scripts/
- `lib.js` — CDP 连接（`CDP_URL` 环境变量）+ 找标签/清理百度残留标签的工具。
- `unlock.js <密码>` — 解锁 PanTools 列表（清搜索框、填密码、点 `#passwordConfirm`）。
- `enumerate.js <起始文件夹> <out.json>` — 递归+分页抓全量文件清单。
- `process-file.js <fsId> [目标目录]` — 单文件转存（也被 run-all 复用）。
- `run-all.js <manifest> <progress> [目标目录]` — 批量转存，按文件夹分组导航，断点续跑。

## 坑位备忘
- **PanTools 密码有防爆破锁（最关键）**：**绝不要 full-reload 列表页**！整页刷新会让列表重新上锁、必须重输密码；反复刷新+重输会触发『密码错误次数过多，请1小时后再试』，被锁 1 小时无法再进。正确做法：**开局解锁一次**，之后只用 **SPA 点击导航**（面包屑 `.breadcrumb-item[0]`=首页 + 点 `.file-item` 进文件夹），列表页全程不 `goto`/`reload`。转存单文件只点分享按钮（开/关百度新标签，列表页不动）。`run-missing.js` 已按此实现；批量中途要换文件夹也走 SPA。另：用 `?path=` 直接拼 URL 导航会再次上锁，且分享内部路径用的是『340G』串而非标题的『280G』，直接拼会密码不匹配——别走 URL 导航。
- **保存成功要按真相核对**：百度 SPA 文件列表是虚拟滚动 + hash 导航有缓存，靠滚动抓名字会漏；**用网盘内部 API 核对**：在已登录的网盘标签页里 `fetch('/api/list?dir=/<目标目录>&num=1000&page=N&web=1&clienttype=0',{credentials:'include'})` 取 `list[].server_filename`，与 manifest 比对得真实缺失清单，再用 `run-missing.js` 补。别信 UI 截图/滚动计数。
- **profile 锁**：系统 Chrome 同一 user-data-dir 不能开两个实例；必须用独立 user-data-dir。
- **端口冲突**：用户 Chrome 可能已监听 9222（会落到 IPv6 造成歧义）；用专用端口并 `curl /json/version` 确认。
- **点击被遮挡**：百度「保存到网盘」被 `div.module-canvas` 拦截 → 用页面内 `el.click()` 而非 Playwright click；模态确认按钮要点**可见**的那个（如 `#passwordConfirm`，别点隐藏同名节点或外层 wrapper）。
- **绝不关用户的 Chrome**：`browser.close()` 会关浏览器；本 skill 脚本一律不调用，靠 `process.exit()` 断开 CDP。
