# Roleflowv2 子命令：init

> 本文件**仅在用户调用 `/roleflowv2 init` 时**由 SKILL.md 按需读取并严格执行，平时不要预加载。
> 作用：在**当前工作目录**脚手架出一套最小可用的 roleflow（v2）骨架。

## 目标产物

在 `当前目录/roleflow/` 下建出如下结构（第一层仅 `context/` 与 `tasks/`）：

```
roleflow/
├── context/
│   ├── project.md              # 项目介绍占位
│   ├── roles/                  # 角色实例层：每个角色一份“引用原型”的壳文件
│   │   ├── common.md           # v2 工件命名规范（本文档 §模板 提供）
│   │   ├── index.md            # 角色清单（本步骤生成）
│   │   └── <各角色>.md         # 全局原型层每个角色对应一份壳，开头引用其原型
│   ├── standards/
│   │   └── index.md            # 占位索引
│   ├── architecture/
│   │   └── index.md
│   ├── domain/
│   │   └── index.md
│   ├── features/
│   │   └── index.md
│   ├── security/
│   │   └── index.md
│   └── workflows/
│       └── index.md
└── tasks/
    └── .gitkeep                # 留空，仅占位让 git 跟踪空目录
```

### 为什么是这些目录

- **第一层只要 `context/` + `tasks/`**：v1 的各角色独立输出目录（`clarifications/`、`explorations/`、`implementation/`、`reviews/`、`testDesign/`、`daily-reports/`、`weekly-reports/` 等）在 v2 里已统一收敛进 `tasks/[版本号]/[任务ID]/`，无需再建。`commands/`、`buleprints`（软链）不属于脚手架。
- **`context/` 下 7 个子目录全建**：`roles` / `standards` / `architecture` / `domain` / `features` / `security` / `workflows` 都被角色或 workflow 引用，且 archivist 角色会维护其中 6 个的 `index.md`。
- **`tasks/` 留空**：不预建 `[版本号]` 子目录、不预建 `taskIndex.md`；这些由首次注册任务时按需创建（见 common.md §10）。
- **`context/roles/` 必须填充全部角色，而非留空**：roleflowv2 主命令 Step 2「命中第一个存在的目录即停」。若 `roleflow/context/roles/` 存在却为空（或缺某个角色），会**遮蔽全局原型层**，导致 `/roleflowv2 <角色>` 找不到该角色文件；且 `tasks/` 落盘根是从命中的 roles 目录推导的。因此必须为全局原型层的**每一个角色**都建出对应实例文件。
- **角色实例文件是“引用原型的壳”，不是原型的拷贝**：每份壳文件开头先用 `> 角色原型：/Users/aaron/code/roleflow/roles/<role>.md` 引好全局原型，再用 `> 公共规范：roleflow/context/roles/common.md` 引好本项目公共规范；正文留“本项目补充”占位。加载该角色时，先读壳文件、并按壳里的「角色原型」指针一并参考原型定义。这样新项目既不遮蔽原型、又保留逐角色做项目特化的入口。

## 执行步骤

### Step 1：前置校验

1. 确认当前工作目录（`pwd`），向用户念出"将在 `<cwd>/roleflow/` 下初始化骨架"。
2. 若 `roleflow/` 已存在：**停止**，不要覆盖。提示用户"当前目录已存在 roleflow/，init 不会覆盖已有内容；如需重建请先手动备份/删除"。
3. 若全局原型层 `/Users/aaron/code/roleflow/roles/` 不存在：提示用户该路径缺失，询问角色文件的拷贝来源后再继续，禁止凭空造角色文件。

### Step 2：建目录

```
mkdir -p roleflow/context/roles \
         roleflow/context/standards \
         roleflow/context/architecture \
         roleflow/context/domain \
         roleflow/context/features \
         roleflow/context/security \
         roleflow/context/workflows \
         roleflow/tasks
```

### Step 3：填充 `context/roles/`

为全局原型层的**每一个角色**（`/Users/aaron/code/roleflow/roles/*.md`，排除 `common.md`、`index.md`）生成一份**引用原型的壳文件**——不是拷贝原型正文，而是建一个开头引用原型、正文留待补充的实例文件。

> **角色集合与数量都不写死**：脚本按 `/Users/aaron/code/roleflow/roles/` 目录里**当前实际存在的 `*.md`** 动态遍历生成，不枚举角色名、不假设固定个数。原型层将来新增 / 删除角色，重新跑一次 `/roleflowv2 init`（或在已存在的项目里只补 `roles/`）就会自动多生成 / 少生成对应壳文件，本脚本无需改动。

可直接用以下脚本一次生成全部壳文件与 `roles/index.md`：

```
SRC=/Users/aaron/code/roleflow/roles
DST=roleflow/context/roles
IDX="$DST/index.md"
printf '# roles/ 目录索引\n\n> 角色实例层。每份角色文件开头引用全局原型层对应角色；正文写本项目特化约束。\n\n## 角色清单\n\n| 实例文件 | 角色原型 | 本项目补充 |\n|------|------|------|\n' > "$IDX"
for f in "$SRC"/*.md; do
  base=$(basename "$f" .md)
  case "$base" in common|index) continue;; esac
  # 取原型 H1 标题，去掉“原型”字样（兼容“…）原型”与“…原型）”两种写法）
  title=$(grep -m1 '^# ' "$f" | sed -e 's/原型）/）/' -e 's/原型[[:space:]]*$//')
  {
    printf '%s\n\n' "$title"
    printf '> 角色原型：%s/%s.md\n' "$SRC" "$base"
    printf '> 公共规范：roleflow/context/roles/common.md\n\n'
    printf '## 本项目补充\n\n'
    printf '（待补充：本项目对该角色的实例化约束，如输出路径、专项检查、命名细则等。在补充之前，加载本角色时请先阅读上方「角色原型」文件并按其执行。）\n'
  } > "$DST/$base.md"
  printf '| [%s.md](%s.md) | %s/%s.md | 待补充 |\n' "$base" "$base" "$SRC" "$base" >> "$IDX"
done
```

生成结果（举例）：`roleflow/context/roles/builder.md`

```
# Builder（实现工程师）

> 角色原型：/Users/aaron/code/roleflow/roles/builder.md
> 公共规范：roleflow/context/roles/common.md

## 本项目补充

（待补充：……在补充之前，加载本角色时请先阅读上方「角色原型」文件并按其执行。）
```

然后写 `roleflow/context/roles/common.md`（用本文档 §模板：common.md 的内容整份写入）——角色壳文件正文里的"common.md §N"引用全靠这份。

### Step 4：写占位文件

1. `roleflow/context/project.md` ← 本文档 §模板：project.md。
2. 以下 6 个子目录各写一份 `index.md` 占位（用 §模板：index.md，把 `<目录名>` 替换为对应目录名）：
   `standards` / `architecture` / `domain` / `features` / `security` / `workflows`。
   （`roles/index.md` 已由 Step 3 生成，不必重写。）
3. `roleflow/tasks/.gitkeep` ← 空文件。

### Step 5：收尾确认

向用户输出（不超过 8 行）：

```
已在 <cwd>/roleflow/ 初始化 roleflow(v2) 骨架：
- context/roles/      ← 为全局原型每个角色生成“引用原型”的壳文件 N 份 + v2 common.md + index.md
- context/{standards,architecture,domain,features,security,workflows}/ ← 各含占位 index.md
- context/project.md  ← 占位，请补充项目背景
- tasks/              ← 留空（首个任务由 /roleflowv2 <角色> <任务名> 注册时创建）
下一步：用 /roleflowv2 <角色名> <任务名称> 开始第一个任务。
```

## 安全约束

- **绝不覆盖**已存在的 `roleflow/`；只在全新目录上脚手架。
- 角色实例文件是**引用原型的壳**（开头 `> 角色原型：/Users/aaron/code/roleflow/roles/<role>.md`），不要把原型正文整段拷进来，也不要凭空编造新角色；必须覆盖全局原型层的全部角色，逐一对应。全局原型层缺失时停下来问用户来源。
- `tasks/` 保持空，不替用户预建任务目录或 taskIndex.md。
- 占位文件内容保持精简，明确标注"待补充"，不要塞入与本项目无关的样例内容。

---

## 模板：common.md

> 整份写入 `roleflow/context/roles/common.md`（覆盖原型自带版本）。

````
# 角色公共规范（实例层）

> 角色公共原型：/Users/aaron/code/roleflow/roles/common.md

本文件统一沉淀**任务级工件命名与存放规范**，所有角色文件不再单独维护这一套规则。

---

## 项目介绍

开始任务前，可阅读 `roleflow/context/project.md` 了解项目背景、架构和核心概念。

---

## 输出语言

所有文档输出均使用**中文**。代码标识符、文件路径、技术术语保持原文。

---

## 任务级工件存放（适用于所有产出 Markdown 工件的角色）

> 凡是会落盘工件的角色，无论 `ad-hoc` 还是 `workflow` 模式，**默认按本节规则落盘**。
> 仅当 orchestrator 在 prompt 中显式指定 artifact 路径时，才以其路径为最高优先级。

### 1. 任务目录路径

```
roleflow/tasks/[版本号]/[任务ID]/
```

- `[版本号]`：从项目根 `package.json` 的 `version` 字段获取（如 `1.0.0`）
- `[任务ID]`：见 §2

### 2. 任务ID（[任务ID]）

格式：`[任务序号][任务名驼峰].[版本号]`

| 段 | 规则 |
|----|------|
| `[任务序号]` | 2 位数字左补零（`01`…）。取该版本目录下已有序号最大值 + 1；目录不存在或为空则 `01` |
| `[任务名驼峰]` | 2–5 个英文单词，越少越好，lowerCamelCase，由中文任务名翻译/概括 |
| `[版本号]` | 同上 |

示例：`01approveList.1.0.0`、`02userProfile.1.0.0`

> 任务ID 的推断 / 确认 / 注册流程由 `/roleflowv2` skill 在加载时一次性完成；角色文件**只在 skill 未介入且会话上下文无任务ID 时**才自行推断。

### 3. 工件文件名

```
[角色名序号][角色名驼峰][工件序号]-[YYYYMMDD].md
```

各段连写，段间无分隔符；仅日期前是 `-`。

| 段 | 规则 |
|----|------|
| `[角色名序号]` | 3 位数字，见 §4 |
| `[角色名驼峰]` | kebab 角色名转 lowerCamelCase，见 §4 |
| `[工件序号]` | 2 位，按「角色 × 任务」独立递增；不同角色互不影响 |
| `[YYYYMMDD]` | 文件**创建当天**本地日期；后续追加内容不改文件名 |

示例：`300planner01-20260101.md`、`400builder01-20260102.md`

### 4. 角色名 → 序号 / 驼峰 映射表

| kebab 角色名 | 序号 | 驼峰名 |
|--------------|------|--------|
| clarifier | `100` | `clarifier` |
| prd-skeptic | `150` | `prdSkeptic` |
| explorer | `200` | `explorer` |
| planner | `300` | `planner` |
| planner-for-style | `350` | `plannerForStyle` |
| builder | `400` | `builder` |
| builder-for-style | `450` | `builderForStyle` |
| critic | `500` | `critic` |
| evaluator-for-e2e | `600` | `evaluatorForE2e` |
| evaluator-for-chrome-ui | `650` | `evaluatorForChromeUi` |
| test-designer | `700` | `testDesigner` |
| test-writer | `800` | `testWriter` |

### 5. 附属子目录

角色产出附属内容（截图、链上 JSON、外部文档快照等）时，**子目录名 = 主报告文件名去掉 `.md` 后缀**，与主报告同级存放。

### 6. 工件序号计算（落盘前）

1. `ls roleflow/tasks/[版本号]/[任务ID]/` 取本角色（按 `[角色名序号][角色名驼峰]` 前缀）已存在的工件。
2. 取其中 `[工件序号]` 最大值 + 1；没有则 `01`。左补零成 2 位。

### 7. 跨角色引用上游工件

到**同一任务目录**按对应 `[角色名序号][角色名驼峰]` 前缀检索；存在多版本时默认取最新（`[工件序号]` 最大者，或文件名内日期最大者）。

### 8. 不属于任务工件的产物

以下按工程惯例落盘，不受本节命名约束：业务源代码、单测、E2E 代码、项目级长期文档（`roleflow/context/...`）。

### 9. 任务目录索引

任务目录 `tasks/[版本号]/[任务ID]/` **不维护 `index.md`**。版本级总索引用 `tasks/[版本号]/taskIndex.md`，见 §10。

### 10. 任务索引（taskIndex.md）维护

每个版本 `roleflow/tasks/[版本号]/taskIndex.md` 作为该版本所有已注册任务的总览，是 `/roleflowv2 switch` 模糊匹配的唯一数据源，**必须与实际任务目录保持一致**。

格式：

```
# Task Index v[版本号]

| 任务ID | 中文名 | 状态 | 创建日期 | 描述 |
|---|---|---|---|---|
| 01approveList.1.0.0 | 授权列表 | active | 2026-01-01 | 首页授权管理 |
```

注册时机：**当且仅当**推断出一个全新任务ID 时，用户确认后**先写 taskIndex.md，再落任何工件**。文件不存在则先 `mkdir -p` + 写表头（`# Task Index v[版本号]` + 表头行 + 分隔行）再追加任务行；已存在则仅追加，不重排已有行。

---

## 路径引用规则

- 角色文件正文引用其他文件，一律用**项目根相对路径**（如 `roleflow/context/standards/coding-standards.md`）。
- 禁止使用 `../` / `./` 类 sibling 相对路径。
- 全局原型层引用使用绝对路径（`/Users/aaron/code/roleflow/roles/xxx.md`）。
````

---

## 模板：project.md

> 写入 `roleflow/context/project.md`。

```
# 项目介绍

> 本文件由 roleflow init 生成的占位骨架，请补充项目实际信息后删除本行。

## 项目背景

（待补充：项目做什么、目标用户、核心价值）

## 架构概览

（待补充：技术栈、模块划分、关键目录）

## 核心概念

（待补充：项目特有的业务名词、约定、术语对照）
```

---

## 模板：index.md

> 写入各 `context/<目录名>/index.md`，把 `<目录名>` 替换为实际目录名。

```
# <目录名>/ 目录索引

> （待补充：本目录文档用途的一句话说明）

## 文件列表

| 文件 | 内容 | 关键主题 |
|------|------|---------|
```
