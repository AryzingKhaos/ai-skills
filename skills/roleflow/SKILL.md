---
name: roleflow
description: "角色加载器。通过 /roleflow [角色名] 加载指定角色的协作规范，让 Claude 按照该角色的职责、行为约束和工作流执行后续任务。角色定义文件按优先级查找：当前项目的 `roleflow/context/roles/` → `docs/link-ai-prompt/roleflow/context/roles/` → `.vscode/link-ai-prompt/roleflow/context/roles/` → `docs/roleflow/context/roles/` → `.vscode/roleflow/context/roles/`，全部不存在时回退到全局原型库 `/Users/aaron/code/roleflow/roles/`。命中第一个存在的目录即停止，不合并。强制精确匹配角色文件名（不含 `.md` 后缀），拒绝模糊匹配；找不到时列出可用角色让用户重新选择。触发场景：'/roleflow builder'、'/roleflow planner'、'/roleflow chat'、'加载 explorer 角色'、'切换到 critic'。"
---

# Roleflow 角色加载器

按用户指定的角色名加载对应的角色定义文件，并把该文件中的规则作为后续协作的最高优先级行为约束。

## 调用语法

```
/roleflow <角色名>
```

- `<角色名>` 必须**完全等于**角色文件去掉 `.md` 后的文件名（大小写、连字符、下划线全部敏感）。
- 不接受任何形式的模糊匹配、部分匹配、别名、拼写纠正。
- 不接受多个角色名同时加载（一次只能激活一个主角色）。

## 工作流

### Step 1: 解析参数

从用户输入中提取 `<角色名>`：

- 如果用户没有给出角色名（例如只输入 `/roleflow`），**不要猜测**，直接执行 Step 2 列出可用角色，并提示"请通过 `/roleflow <角色名>` 指定要加载的角色"。
- 角色名前后空白字符要 trim，但中间字符原样保留。

### Step 2: 定位角色目录

按以下**优先级顺序**确定角色目录（命中即停止，不合并）。判定规则：依次 `ls` 检查每个候选目录是否存在，**第一个存在的目录即为生效目录**，不再继续往下查。

**项目实例层**（按从上到下顺序判定，全部相对于当前工作目录）：

1. `roleflow/context/roles/`
2. `docs/link-ai-prompt/roleflow/context/roles/`
3. `.vscode/link-ai-prompt/roleflow/context/roles/`
4. `docs/roleflow/context/roles/`
5. `.vscode/roleflow/context/roles/`

**全局原型层**（仅当上面 5 个项目实例层目录全部不存在时使用）：

6. `/Users/aaron/code/roleflow/roles/`

如果以上 6 个目录全部不存在，告知用户"未找到任何 roleflow 角色目录"并停止。

> **注意**：项目实例层一旦命中就**完全覆盖**全局原型层与其他项目实例层候选目录，不做合并、不做补全。这是为了避免角色定义在多个目录间混入造成行为不一致。
>
> **建议实现**：用一条 `ls -d <候选路径> 2>/dev/null` 串联或循环判断，命中第一个就锁定为生效目录。

### Step 3: 精确匹配角色文件

在选定目录下查找文件 `<角色名>.md`：

- **命中（恰好一个文件）**：进入 Step 4。
- **未命中**：执行下方"未命中处理"流程。

**禁止做的事**：
- ❌ 不允许做大小写不敏感匹配（例如 `Builder` 不会命中 `builder.md`）。
- ❌ 不允许做前缀/后缀/包含匹配（例如 `build` 不会命中 `builder.md`）。
- ❌ 不允许做拼写纠正（例如 `bulider` 不会命中 `builder.md`）。
- ❌ 不允许在用户给出歧义输入时自动选择"最接近的一个"。

#### 未命中处理

1. 用 `ls <选定目录>/*.md` 列出该目录下所有可用角色文件。
2. 输出格式如下：

   ```
   未找到角色 "<用户输入>"。当前 <目录路径> 下可用的角色：
   - archivist
   - builder
   - chat
   - ...

   请使用 /roleflow <角色名> 重新指定（角色名需精确匹配，区分大小写）。
   ```

3. **停止执行**，等待用户重新输入。不要自作主张加载任何角色。

### Step 4: 加载并应用角色

1. 用 `Read` 工具读取角色文件 `<选定目录>/<角色名>.md` 的完整内容。
2. 同时检查并读取 `<选定目录>/common.md`（如果存在）—— 它是所有角色共享的基础协作原则，必须叠加到当前角色之上。
3. 向用户输出**简短确认**（不超过 3 行），格式如下：

   ```
   已加载角色：<角色名>（来源：<项目实例层 | 全局原型层>）
   职责摘要：<从角色文件首段提炼的一句话>
   后续所有任务将按此角色的规范执行。
   ```

4. 在后续对话中：
   - 把角色文件 + `common.md` 的规则视为**最高优先级行为约束**，凌驾于一般默认行为之上。
   - 当角色规则与项目 CLAUDE.md 冲突时，**优先遵循角色规则**，并在必要时向用户说明冲突点。
   - 当角色规则与用户即时指令冲突时，停下来询问，不要自行选择。

## 边界与禁止事项

- **不可同时激活两个角色**：如果用户在已加载某个角色后再次执行 `/roleflow <新角色>`，视为切换角色 —— 丢弃旧角色规则，按新角色规则执行，并明确告知用户"已从 <旧角色> 切换到 <新角色>"。
- **不可静默加载**：每次加载都必须输出 Step 4 中的确认信息，让用户清楚当前激活的角色。
- **不可篡改角色文件**：本 skill 只负责加载，不负责创建、编辑、删除角色文件。如果用户要求修改角色定义，引导其手动编辑 `roleflow/context/roles/` 或 `/Users/aaron/code/roleflow/roles/` 下的文件。
- **不可跨目录混用**：一次加载只读取一个目录下的文件，不会从全局原型层补全项目实例层缺失的角色。

## 示例

### 正常加载

用户：`/roleflow builder`

执行：
1. 依次检查项目实例层 5 个候选目录：
   - `./roleflow/context/roles/` → 不存在
   - `./docs/link-ai-prompt/roleflow/context/roles/` → 不存在
   - `./.vscode/link-ai-prompt/roleflow/context/roles/` → 不存在
   - `./docs/roleflow/context/roles/` → 不存在
   - `./.vscode/roleflow/context/roles/` → 不存在
2. 回退到全局原型层 `/Users/aaron/code/roleflow/roles/` → 存在
3. 查找 `builder.md` → 命中
4. 读取 `builder.md` 与 `common.md`
5. 输出：

   ```
   已加载角色：builder（来源：全局原型层）
   职责摘要：根据 Spec 编写实现代码，并维护任务状态。
   后续所有任务将按此角色的规范执行。
   ```

### 拒绝模糊匹配

用户：`/roleflow Builder`

执行：
1. 定位目录成功
2. 查找 `Builder.md` → 未命中（`builder.md` 大小写不同，不算命中）
3. 输出可用角色清单，要求用户重新指定。**不会**自动加载 `builder.md`。

### 缺参数

用户：`/roleflow`

执行：
1. 没有角色名
2. 列出当前生效目录下所有可用角色
3. 提示用户用 `/roleflow <角色名>` 重新调用

### 角色切换

用户先后执行 `/roleflow planner`，`/roleflow builder`：

第二次执行时输出：

```
已从 planner 切换到 builder（来源：全局原型层）
职责摘要：根据 Spec 编写实现代码，并维护任务状态。
后续所有任务将按 builder 的规范执行。
```
