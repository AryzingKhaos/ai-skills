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
│   │   ├── common.md           # 实例层公共规范（引用 base/common.md；本文档 §模板 提供）
│   │   ├── index.md            # 角色清单（本步骤生成）
│   │   └── <各角色>.md         # 全局原型层每个角色对应一份壳，开头引用其原型
│   ├── standards/              # 从 templates/standards/ 预置（init 唯一带实际内容的目录）
│   │   ├── coding-standards.md       # 预置内容
│   │   ├── code-style.md             # 预置内容
│   │   ├── style-standards.md        # 预置内容
│   │   ├── test-case-standards.md    # 预置内容
│   │   ├── common-mistakes.md        # 留空
│   │   ├── component-index.md        # 留空
│   │   └── index.md                  # 标准目录索引（随模板带入）
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
- **`context/standards/` 预置实际内容**：从 `/Users/aaron/code/roleflow/templates/standards/` 拷入 6 份标准文件（`coding-standards` / `code-style` / `style-standards` / `test-case-standards` 有内容，`common-mistakes` / `component-index` 留空）+ `index.md`，是 init 唯一带实际规范内容的目录；其余 context 子目录只给占位 `index.md`。
- **`tasks/` 留空**：不预建 `[版本号]` 子目录、不预建 `taskIndex.md`；这些由首次注册任务时按需创建（见 base/common.md §10）。
- **`context/roles/` 必须填充全部角色，而非留空**：roleflowv2 主命令 Step 2「命中第一个存在的目录即停」。若 `roleflow/context/roles/` 存在却为空（或缺某个角色），会**遮蔽全局原型层**，导致 `/roleflowv2 <角色>` 找不到该角色文件；且 `tasks/` 落盘根是从命中的 roles 目录推导的。因此必须为全局原型层的**每一个角色**都建出对应实例文件。
- **角色实例文件是“引用原型的壳”，不是原型的拷贝**：每份壳文件开头先用 `> 角色原型：/Users/aaron/code/roleflow/roles/<role>.md` 引好该角色的全局原型，再用 `> 公共原型：/Users/aaron/code/roleflow/roles/base/common.md` 引好全局公共基类（任务工件命名 §1–§10 + 协作原则），最后用 `> 公共规范：roleflow/context/roles/common.md` 引好本项目公共规范；正文留“本项目补充”占位。加载该角色时，先读壳文件，并按其中的「角色原型」「公共原型」指针一并参考。这样新项目既不遮蔽原型、又保留逐角色做项目特化的入口。

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
    printf '> 公共原型：%s/base/common.md\n' "$SRC"
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
> 公共原型：/Users/aaron/code/roleflow/roles/base/common.md
> 公共规范：roleflow/context/roles/common.md

## 本项目补充

（待补充：……在补充之前，加载本角色时请先阅读上方「角色原型」文件并按其执行。）
```

然后写 `roleflow/context/roles/common.md`（实例层公共规范，用本文档 §模板：common.md 写入）——它只放项目介绍 / 输出语言 / 路径引用规则，任务工件命名 §1–§10 指回 `base/common.md`，不再重复那套规范（角色壳文件正文里引用的"§N"也指向 `base/common.md`）。

### Step 4：填充 `context/standards/`（从模板预置）

把 `/Users/aaron/code/roleflow/templates/standards/` 整目录拷入：

```
cp /Users/aaron/code/roleflow/templates/standards/*.md roleflow/context/standards/
```

拷入 7 份文件：

- `coding-standards.md` / `code-style.md` / `style-standards.md` / `test-case-standards.md` —— 预置内容（源自 TronLink 项目，可按本项目改写）
- `common-mistakes.md` / `component-index.md` —— **留空**（0 字节），由项目自行积累 / 补充
- `index.md` —— 标准目录索引（随模板带入）

> 这是 init 唯一**预置实际内容**的目录。要改新项目的默认规范，直接改 `/Users/aaron/code/roleflow/templates/standards/`，init 照拷，无需改本文档。

### Step 5：写占位文件

1. `roleflow/context/project.md` ← 本文档 §模板：project.md。
2. 以下 5 个子目录各写一份 `index.md` 占位（用 §模板：index.md，把 `<目录名>` 替换为对应目录名）：
   `architecture` / `domain` / `features` / `security` / `workflows`。
   （`roles/index.md` 由 Step 3 生成、`standards/index.md` 由 Step 4 随模板带入，均不必重写。）
3. `roleflow/tasks/.gitkeep` ← 空文件。

### Step 6：收尾确认

向用户输出（不超过 8 行）：

```
已在 <cwd>/roleflow/ 初始化 roleflow(v2) 骨架：
- context/roles/      ← 为全局原型每个角色生成“引用原型 + base”的壳文件 N 份 + 实例层 common.md + index.md
- context/standards/  ← 从 templates 预置 4 份规范 + 2 份留空（common-mistakes / component-index）+ index.md
- context/{architecture,domain,features,security,workflows}/ ← 各含占位 index.md
- context/project.md  ← 占位，请补充项目背景
- tasks/              ← 留空（首个任务由 /roleflowv2 <角色> <任务名> 注册时创建）
下一步：用 /roleflowv2 <角色名> <任务名称> 开始第一个任务。
```

## 安全约束

- **绝不覆盖**已存在的 `roleflow/`；只在全新目录上脚手架。
- 角色实例文件是**引用原型的壳**（开头 `> 角色原型：/Users/aaron/code/roleflow/roles/<role>.md`），不要把原型正文整段拷进来，也不要凭空编造新角色；必须覆盖全局原型层的全部角色，逐一对应。全局原型层缺失时停下来问用户来源。
- `tasks/` 保持空，不替用户预建任务目录或 taskIndex.md。
- `context/standards/` 的内容**直接拷贝** `/Users/aaron/code/roleflow/templates/standards/`，不要手写或改写模板内容；`common-mistakes.md` / `component-index.md` 保持空文件。
- 其余占位文件（project.md、各 index.md）内容保持精简，明确标注"待补充"，不要塞入与本项目无关的样例内容。

---

## 模板：common.md

> 写入 `roleflow/context/roles/common.md`（实例层公共规范，引用 base/common.md）。

````
# 角色公共规范（实例层）

> 角色公共原型：/Users/aaron/code/roleflow/roles/base/common.md

本文件是本项目对全局原型 `base/common.md` 的实例化补充：项目介绍、输出语言、路径引用规则。**通用的任务级工件命名与存放规范（§1–§10）见 `base/common.md`，本文件不重复。**

---

## 项目介绍

开始任务前，可阅读 `roleflow/context/project.md` 了解项目背景、架构和核心概念。

---

## 输出语言

所有文档输出均使用**中文**。代码标识符、文件路径、技术术语保持原文。

---

## 任务级工件存放（本项目落地）

通用规范（§1–§10：任务目录、任务ID、工件文件名、角色名→序号映射、taskIndex.md 维护等）见 `/Users/aaron/code/roleflow/roles/base/common.md`。

本项目落地：base 里通用的 `roleflow/` 根，在本项目即当前 `roleflow/`（实例层若落在 `docs/.../roleflow/` 等则相应替换）；`[版本号]` 从根目录 `package.json` 的 `version` 读取。

---

## 路径引用规则

- 角色文件正文引用其他文件，一律用**项目根相对路径**（如 `roleflow/context/standards/coding-standards.md`）。
- 禁止使用 `../` / `./` 类 sibling 相对路径。
- 全局原型层引用使用绝对路径（`/Users/aaron/code/roleflow/roles/base/common.md`、`/Users/aaron/code/roleflow/roles/<role>.md`）。
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
