# Roleflowv2 子命令：switch

> 本文件**仅在用户调用 `/roleflowv2 switch <任务名>` 时**由 SKILL.md 按需读取并严格执行，平时不要预加载。
> 作用：在**当前会话已加载角色不变**的前提下，把任务上下文切换到另一个**已注册任务**。

## 工作流

### Step S1: 解析

- 用户输入：`/roleflowv2 switch <查询串>`。
- 用首个空白把 `switch` 与查询串分开；查询串两端 trim，中间保留原样。
- 关键字 `switch` 必须**字面精确**匹配（区分大小写）。若用户输入 `Switch` / `SWITCH` / `swich` 等，**不**进入 switch 流程 —— 这时回到主命令解析：尝试把首段当作 `<角色名>` 走 SKILL.md Step 2–3；按角色名也匹不上则提示"未找到角色 / 不是合法的 switch 关键字"，停止。
- 查询串为空：列出当前 taskIndex.md 全表，提示"请通过 `/roleflowv2 switch <任务名>` 指定"，停止。

### Step S2: 校验前置

- **会话内必须已加载角色**（即此前已成功执行过 `/roleflowv2 <角色名> ...`）。否则提示：

  ```
  尚未加载任何角色。请先用 /roleflowv2 <角色名> [任务名称] 加载角色，再调用 switch。
  ```

  停止。

### Step S3: 解析版本号

- 默认读 `package.json` 的 `version` 字段。
- 查询串末尾如带 semver，按其覆盖（并从查询串里去掉该尾段）。
- 都没有：向用户索要版本号，禁止瞎编。

### Step S4: 定位 taskIndex.md

- 跟随当前会话已确定的 `<tasks 根>`（即上次主命令命中的根）。
- 路径：`<tasks 根>/[版本号]/taskIndex.md`。
- **文件不存在**：

  ```
  <版本号> 版本尚无任务索引（taskIndex.md 不存在）。请先用 /roleflowv2 <角色名> <任务名称> 注册任务后再切换。
  ```

  停止。

### Step S5: 模糊匹配

- 解析 taskIndex.md 表格，逐行抽取 `任务ID`、`中文名` 两列（其余字段不参与匹配）。
- 把查询串和这两列同时做**小写化**（中文字符不受影响），按**子串包含**判定：查询串作为子串、目标列作为母串，命中即算。
  - 例：查询串 `defi` 同时命中 `02defiIndexdbCache.4.10.0` 和 `04defiGaClick.4.10.0`。
  - 例：查询串 `授权` 命中 `01approvalList.4.10.0`（中文名"授权列表"）。
  - 例：查询串 `approval` 命中 `01approvalList.4.10.0`（任务ID 含 `approval`）。
- 命中集合：
  - **恰好 1 条** → 进入 Step S7。
  - **0 条** → 列出当前 taskIndex.md 全表的简短清单（任务ID + 中文名），提示用户重新指定，停止。
  - **多于 1 条** → 进入 Step S6 让用户选。

### Step S6: 候选去歧义（多命中分支）

输出：

```
查询 "<查询串>" 命中多个任务，请回复任务ID 或更精确的子串：
- 01approvalList.4.10.0 — 授权列表
- 04defiGaClick.4.10.0 — defi GA 点击异常
- …
```

停止等用户回复。用户回复任务ID 或更精确子串后回到 Step S5 重判。

### Step S7: 切换并确认

- 把会话级 `<任务ID>` 和 `<工件目录>` 替换为命中任务的；**当前角色保持不变**。
- 输出确认（不超过 5 行）：

  ```
  已切换到任务：<任务ID>
  - 中文名：<中文名>
  - 角色：<当前角色>（继承自上次加载，未变更）
  - 工件目录：<完整项目根相对路径>
  后续工件按此任务落盘。
  ```

## 边界与禁止事项（switch 专属）

- **switch 不动角色**：`/roleflowv2 switch <任务名>` **只换任务**，不换角色。如需同时换角色，先 switch 再 `/roleflowv2 <新角色>`（不带任务名即可继承新任务）。
- **不可对 `switch` 关键字做模糊匹配**：仅 `<任务名>` 模糊，关键字本身字面精确。
- **不可静默切换**：必须输出 Step S7 的确认信息（含任务ID 与工件目录）。

## 示例

### 示例 A：中文模糊匹配（单命中）

会话内已加载 `/roleflowv2 builder 审批列表`（任务 `01approveList.4.10.0`）。

用户：`/roleflowv2 switch 观察钱包`

执行：
1. 关键字 `switch` 字面命中。
2. 角色已加载（builder），版本号 `4.10.0`。
3. 读 `docs/link-ai-prompt/roleflow/tasks/4.10.0/taskIndex.md`。
4. 子串包含 `观察钱包`（小写化）扫描两列：命中 1 条 `03watchWalletDapp.4.10.0`（中文名"观察钱包连接网站"）。
5. 切换并输出：

   ```
   已切换到任务：03watchWalletDapp.4.10.0
   - 中文名：观察钱包连接网站
   - 角色：builder（继承自上次加载，未变更）
   - 工件目录：docs/link-ai-prompt/roleflow/tasks/4.10.0/03watchWalletDapp.4.10.0/
   后续工件按此任务落盘。
   ```

### 示例 B：多命中去歧义

用户：`/roleflowv2 switch defi`

执行：
1. 关键字命中。
2. 在 taskIndex.md 中子串包含 `defi` → 命中 2 条：`02defiIndexdbCache.4.10.0`、`04defiGaClick.4.10.0`。
3. 输出：

   ```
   查询 "defi" 命中多个任务，请回复任务ID 或更精确的子串：
   - 02defiIndexdbCache.4.10.0 — defi添加indexDB
   - 04defiGaClick.4.10.0 — defi GA 点击异常
   ```

4. 等用户回复 → 再走一次 Step S5。

### 示例 C：关键字拼错（不进入 switch）

用户：`/roleflowv2 Switch 授权列表`

执行：
1. 关键字 `Switch` 与字面 `switch` 不等（大小写不同）→ **不进入** switch 流程。
2. 回退到主命令：把首段 `Switch` 当作角色名 → 在当前角色目录下找 `Switch.md` → 未命中。
3. 列出可用角色，提示用户重新调用。**不会**因为"看着像 switch"而自动模糊纠正。
