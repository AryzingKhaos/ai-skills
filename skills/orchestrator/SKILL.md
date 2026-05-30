---
name: orchestrator
description: "工作流编排器。通过 /orchestrator <工作流名> <任务描述> 启动多角色 subagent 编排，支持 continue（续跑）/ rerun（重跑）/ status（查询）/ 不带参（列出可用工作流）。触发场景：'/orchestrator feature-medium 加个 dapp 超时提示'、'/orchestrator continue 2026-05-22-dapp-connection'、'/orchestrator rerun <task-id>'、'/orchestrator status <task-id>'、'/orchestrator'（列出工作流）、'启动工作流'、'用 onchain-feature 跑这个任务'。"
---

# Orchestrator 工作流编排器

按用户显式指定的 workflow 文件，编排多角色 subagent 逐步完成复杂任务。每个产出工件落盘到统一的 artifact 目录，在 mandatory gate 与开放问题处暂停等用户决策。

## 关键约束（详见下文各节）

- **编排流程**：按 workflow 文件中定义的角色顺序逐个派发 subagent，在 mandatory gate 和开放问题处暂停等用户决策（见 [工作流](#工作流)）。
- **目录查找优先级**：workflow 文件按固定优先级目录查找，命中第一个存在的即停止、不合并（见 Step 2）。
- **精确匹配**：workflow 名强制精确匹配文件名（不含 `.md` 后缀），拒绝任何模糊匹配（见 Step 3.1 与「边界与禁止事项」）。
- **subagent 模型**：所有派发的 subagent 强制使用 Opus 4.7 + xhigh（ultrathink）思考模式（见「Subagent 派发要求」）。

## 调用语法

| 触发 | 行为 |
|---|---|
| `/orchestrator <工作流名> <任务描述>` | 启动新任务 |
| `/orchestrator continue <task-id>` | 续跑（用户已修改开放问题） |
| `/orchestrator rerun <task-id>` | 重跑（artifact 升 v+1） |
| `/orchestrator status <task-id>` | 查询任务状态 |
| `/orchestrator` 不带参数 | 列出可用 workflow + 进行中 task |

- `<工作流名>` 必须**完全等于** workflow 文件去掉 `.md` 后的文件名（大小写、连字符、下划线全部敏感）。
- 不接受任何模糊匹配、部分匹配、别名、拼写纠正。
- 不接受多 workflow 同时运行（一次只跑一个 task）。

## 工作流

### Step 1: 解析参数

从用户输入中识别命令类型：

| 第一个 token | 命令 | 后续参数 |
|---|---|---|
| 已知 workflow 名 | **新任务** | 任务描述（必填） |
| `continue` | **续跑** | task-id（必填） |
| `rerun` | **重跑** | task-id（必填） |
| `status` | **查询** | task-id（必填） |
| 空 | **列出** | 无 |

- 如果用户描述里没出现明确的 workflow 名（例如"加个 dapp 连接超时提示"），**不要猜测 workflow**，进入 Step 2 列出可用 workflow，要求用户显式指定。
- 角色名 / workflow 名 / task-id 前后空白字符 trim，但中间字符原样保留。

### Step 2: 定位 workflows 目录

按以下**优先级顺序**确定 workflow 目录（命中即停止，不合并）。判定规则：依次 `ls` 检查每个候选目录是否存在，**第一个存在的目录即为生效目录**，不再继续往下查。

**项目实例层**（按从上到下顺序判定，全部相对于当前工作目录）：

1. `roleflow/context/workflows/`
2. `docs/link-ai-prompt/roleflow/context/workflows/`
3. `.vscode/link-ai-prompt/roleflow/context/workflows/`
4. `docs/roleflow/context/workflows/`
5. `.vscode/roleflow/context/workflows/`

如果以上 5 个目录全部不存在，告知用户"未找到任何 roleflow workflows 目录，请先在项目中建立 workflows/ 目录并放入 `_orchestrator.md` 与 workflow 文件"，**停止执行**。

> **注意**：workflows 目录不引入全局原型层回退。workflow 与角色组合具有强项目相关性，必须在项目内显式定义。

> **建议实现**：用一条 `ls -d <候选路径> 2>/dev/null` 串联或循环判断，命中第一个就锁定为生效目录。

### Step 3: 命令分发

#### 3.1 新任务

1. **精确匹配 workflow 文件**：在选定目录下查找 `<工作流名>.md`。
2. **命中**：进入 Step 4 加载 `_orchestrator.md`，按其规范执行新任务流程。
3. **未命中**：列出该目录下所有 workflow 文件（去掉 `_` 开头的内部文件如 `_orchestrator.md`、`_router.md`、`index.md`），要求用户重新指定。

**禁止做的事**（与 roleflow 一致）：
- ❌ 不允许大小写不敏感匹配
- ❌ 不允许前缀/后缀/包含匹配
- ❌ 不允许拼写纠正
- ❌ 不允许歧义输入时自动选择"最接近的一个"

#### 3.2 续跑（continue）

1. 找到 `docs/link-ai-prompt/roleflow/artifact/<version>/<task-id>/` 下的**最高版本号目录**（如 v3）。
2. 读取该目录 `README.md`，确认上次中断点与原因。
3. 扫描所有 artifact 文件中的 `<!-- OPEN_QUESTION ... severity=blocking ... -->` 标记。
4. 检查每个 blocking question 是否已替换为 `<!-- RESOLVED ... -->`。
5. 仍有未回填的 blocking → 列出未回填项，要求用户先编辑文件，**停止执行**。
6. 全部已回填 → 进入 Step 4 加载 `_orchestrator.md`，按其规范从下一步继续。
7. **关键**：续跑使用**同一版本号目录**，不开新 v。

#### 3.3 重跑（rerun）

1. 找到 artifact 目录下最高版本号（如 v3），创建 v4。
2. 进入 Step 4 加载 `_orchestrator.md`，按其规范执行重跑流程（含"是否复用部分旧 artifact"询问）。
3. **关键**：v3 及之前的目录**不删**，永久保留。

#### 3.4 状态查询（status）

1. 读取 `docs/link-ai-prompt/roleflow/artifact/<version>/<task-id>/` 下最新版本目录的 `README.md`。
2. 输出 README.md 中的"步骤进度"、"开放问题"、"Gate 决策日志"三段内容。
3. **不进入** Step 4，仅查询不派发。

#### 3.5 列出（无参数）

1. 列出选定 workflows 目录下所有非 `_` 开头的 `.md` 文件作为可选 workflow。
2. 列出 `docs/link-ai-prompt/roleflow/artifact/<version>/` 下所有未完成的 task。
3. 输出格式：

   ```
   可用 workflow（来自 <目录路径>）：
   - feature-medium     中等规模新功能开发
   - onchain-feature    TronLink 链上专项
   - bugfix             普通 bug fix
   - ...

   进行中 task：
   - 2026-05-22-dapp-connection  (feature-medium@v1, 暂停在 step 2)
   - ...

   请使用 /orchestrator <工作流名> <任务描述> 启动新任务，
   或 /orchestrator continue <task-id> 续跑。
   ```

4. **停止执行**，等待用户重新输入。

### Step 4: 加载并应用 _orchestrator.md

1. 用 `Read` 工具读取 `<选定 workflows 目录>/_orchestrator.md` 的完整内容。
2. 用 `Read` 读取 workflow 文件本体（新任务/重跑场景）。
3. 同时读取 `<选定目录>/index.md`（如存在）作为参考。
4. 向用户输出**简短确认**（不超过 5 行）：

   ```
   已启动 orchestrator
   workflow：<工作流名>@v<N>（来源：<目录路径>）
   task-id：<task-id>
   artifact 目录：docs/link-ai-prompt/roleflow/artifact/<version>/<task-id>/v<N>/
   下一步：<step seq>. <role>（gate: <type>）
   ```

5. 在后续对话中**严格按照 `_orchestrator.md` 的规范执行**，包括：
   - 主流程（解析 → 创建目录 → 写 README → 逐步派发 → 处理状态 → 处理 gate → 汇总）
   - 4 状态报告处理矩阵
   - 续跑/重跑/中断检测
   - artifact 目录约定
   - runs/ 审计日志

## Subagent 派发要求（强制）

**所有通过 Task tool 派发的 subagent 必须满足以下两条**：

### 1. 模型固定为 Opus 4.7

Task tool 调用时 `model` 参数 **必须**设为 `"opus"`。在当前用户环境中此值映射到 Claude Opus 4.7（具体模型 ID：`claude-opus-4-7`）。

```
Task({
  description: "...",
  subagent_type: "general-purpose",  // 或角色对应的专属类型
  model: "opus",                      // 强制 opus
  prompt: "..."
})
```

不允许使用 `sonnet`、`haiku` 或省略 `model` 参数（省略会继承主对话模型，可能不是 opus）。

### 2. 思考深度固定为 xhigh

派发给 subagent 的 prompt **必须**在开头包含以下指令：

```
**思考深度**：本次任务请使用 ultrathink（xhigh 思考模式）。在落笔产出前，先深度推理至少 N 步：列出已知约束、识别隐含风险、对比 2-3 个备选方案、最后给出选定方案与理由。
```

`ultrathink` 是 Claude Code 识别的最高思考深度关键词，会触发 subagent 进入 xhigh 推理预算。

### 3. 派发模板（汇总）

```
Task({
  description: "[ROLE] step <N>: <一句话>",
  subagent_type: "general-purpose",
  model: "opus",
  prompt: `
**思考深度**：本次任务请使用 ultrathink（xhigh 思考模式）。在落笔产出前，先深度推理：列出约束、识别隐含风险、对比备选方案、给出选定方案与理由。

你是被 orchestrator 派发的 subagent，以 [ROLE_NAME] 身份处理任务。

## 你的角色规范

读取并严格遵循：
@docs/link-ai-prompt/roleflow/context/roles/[ROLE].md

## 任务背景

- 用户原始请求：[USER_REQUEST_ONELINE]
- 项目版本：[VERSION]
- 任务 ID：[TASK_ID]
- 当前迭代：[vN]
- artifact 目录：[ARTIFACT_DIR]
- 当前 workflow：[WORKFLOW_NAME]

## 输入工件（已落盘，自行读取）

- [PATH_1]
  hint: [一句话 hint]

## 你的输出路径

强制写到：[ARTIFACT_DIR]/[SEQ].[ROLE].md

（覆盖角色文件里默认的输出路径，workflow 模式优先级最高）

## 开放问题（open questions）

如有需用户决策的问题，必须在文档中显式标记：

<!-- OPEN_QUESTION: id=oq-XXX, severity=blocking|important|info -->
问题描述... 选项 A... 选项 B... 建议...
<!-- /OPEN_QUESTION -->

任何 blocking 都会让 orchestrator 暂停。

## 完成后强制报告

Status: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
Artifact: [输出文件路径]
Open questions: <数量；如 0 或 2/blocking>
Key decisions: <2-3 bullets>
Hint for next role: <一句话>
Concerns: <如有>

任何不确定都用 DONE_WITH_CONCERNS。Never silently produce work you're unsure about.
`
})
```

## 边界与禁止事项

- **不可模糊匹配 workflow 名**：用户没显式给就要求用户给，不要替用户选。
- **不可静默派发 subagent**：每次派发前都向用户告知"即将派发 [ROLE] 处理 step <N>"。
- **不可使用 sonnet / haiku**：subagent 模型强制 opus 4.7。
- **不可省略 ultrathink 指令**：subagent prompt 必须含 xhigh 思考指令。
- **不可跳过 mandatory gate**：必须停下展示给用户、等用户决策。
- **不可在 BLOCKED / DONE_WITH_CONCERNS / blocking open question 存在时自动进下一步**：必须暂停。
- **不可删除任何 artifact 旧版本目录**（v1、v2 ...）：rerun 升版本，旧版本永久保留。
- **不可篡改 workflow 文件 / 角色文件**：本 skill 只负责调度执行，不创建/编辑/删除规范文件。

## 示例

### 正常启动新任务

用户：`/orchestrator feature-medium 加个 DApp 连接超时提示`

执行：
1. 解析：workflow=`feature-medium`，任务=`加个 DApp 连接超时提示`
2. 定位 workflows 目录 → `docs/link-ai-prompt/roleflow/context/workflows/`
3. 精确匹配 `feature-medium.md` → 命中
4. 读 `_orchestrator.md`、`feature-medium.md`、`index.md`
5. 询问用户确认 task-id（建议：`2026-05-22-dapp-connect-timeout`）
6. 创建 artifact 目录 + README.md
7. 派发 step 1 (explorer) subagent，prompt 含 ultrathink 指令，model=opus
8. 等 subagent 返回 → 按 gate 类型决定停 / 继续

### 拒绝模糊匹配

用户：`/orchestrator FeatureMedium 加个超时`

执行：
1. 解析：workflow=`FeatureMedium`
2. 精确匹配 `FeatureMedium.md` → 未命中（大小写不同）
3. 列出可用 workflow，要求用户重新指定。**不会**自动加载 `feature-medium.md`。

### 续跑（用户已回填开放问题）

用户：`/orchestrator continue 2026-05-22-dapp-connection`

执行：
1. 找到 `artifact/4.9.0/2026-05-22-dapp-connection/v1/`
2. 读 `README.md`，看到上次停在 step 2 等 oq-002
3. 扫描 002.plan.md 找 `<!-- OPEN_QUESTION id=oq-002 ... -->` → 已被 RESOLVED 块替换
4. 所有 blocking 已回填 → 加载 `_orchestrator.md`，从 step 3 继续派发

### 续跑失败（开放问题未回填）

用户：`/orchestrator continue 2026-05-22-dapp-connection`

执行：
1. 找到 v1 目录
2. 扫描发现 oq-002 仍为 OPEN_QUESTION 状态
3. 输出：

   ```
   无法续跑：以下 blocking 开放问题尚未回填决策

   - oq-002 (在 002.plan.md)
     问题：TIP-1102 超时阈值定多少？
     位置：docs/.../v1/002.plan.md:42

   请编辑对应文件，把 OPEN_QUESTION 块替换为 RESOLVED 块后重试。
   ```

4. **停止执行**，不派发任何 subagent。

### 重跑

用户：`/orchestrator rerun 2026-05-22-dapp-connection`

执行：
1. 找到现有最高版本 v2
2. 创建 v3
3. 询问用户：
   - 是否复用 v2 的 001.exploration.md？（如只想重做后面几步）
   - 是否从头开始？
4. 按用户选择拷贝部分旧 artifact 到 v3（标 `<!-- COPIED FROM v2 -->`）
5. 按 `_orchestrator.md` 规范从指定 step 开始派发

### 列出（无参数）

用户：`/orchestrator`

执行：
1. 列出 workflows/ 下所有非 `_` 开头的文件
2. 列出 artifact/<version>/ 下所有未完成 task
3. 提示用户用具体命令重新调用
4. **停止执行**

### 缺 workflow 名

用户：`/orchestrator 加个 dapp 超时提示`

执行：
1. 解析：第一个 token `加个` 不是已知 workflow / continue / rerun / status
2. **不要猜测** workflow 名
3. 列出可用 workflow + 提示用户：

   ```
   未识别 workflow 名。请明确指定，例如：
   /orchestrator feature-medium 加个 dapp 超时提示
   /orchestrator bugfix <bug 描述>

   可用 workflow（来自 docs/link-ai-prompt/roleflow/context/workflows/）：
   - feature-medium
   - onchain-feature
   - bugfix
   ```

4. **停止执行**。
