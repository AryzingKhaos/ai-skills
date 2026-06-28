# llmWikis 项目结构图（D2）

> 在 **Obsidian** 里看：安装社区插件「D2」，下面的 ```d2 代码块会自动渲染成图。
> 在 **VS Code** 里看：直接打开同目录的 `llmwiki-structure.d2`，装「D2」扩展后用预览面板。
> 设计说明：7 个 library 结构同构，图里展开 `aiagentWiki` 作代表，其余 6 个折叠成网格。

```d2
direction: down

classes: {
  doc:   { shape: page;     style: { fill: "#0d0e0f"; stroke: "#6366F1" } }
  cmd:   { shape: hexagon;  style: { fill: "#F0F9FF"; stroke: "#0EA5E9" } }
  store: { shape: cylinder; style: { fill: "#FEF9C3"; stroke: "#CA8A04" } }
}

title: |md
  ## llmWikis · 知识库 monorepo
  raw 原始资料 → **ingest** 编译 → wiki 结构化 → **query** 检索
| { near: top-center }

governance: "治理层 · 角色与边界（根目录）" {
  style: { fill: "#FAF5FF"; stroke: "#9333EA"; stroke-dash: 3 }
  claude: "CLAUDE.md\n通用角色：Wiki 管理员"; claude.class: doc
  agent:  "AGENT.md\n能力边界";              agent.class: doc
}

commands: "script/commands/ · 命令唯一权威说明" {
  style: { fill: "#F0F9FF"; stroke: "#0284C7" }
  ingest;          ingest.class: cmd
  check_uningest;  check_uningest.class: cmd
  query;           query.class: cmd
  raw_query;       raw_query.class: cmd
  lint;            lint.class: cmd
  batch_lint;      batch_lint.class: cmd
}

lib: "aiagentWiki（library · 展开示例）" {
  style: { fill: "#ECFDF5"; stroke: "#059669" }

  raw: "raw/ · 原始资料\n(*.pdf / 文章 / images)"; raw.class: store

  wiki: "wiki/ · 编译后" {
    style: { fill: "#D1FAE5"; stroke: "#10B981" }
    sources:  "sources/\n正式结构化摘要";              sources.class: doc
    fragment: "fragment/\n碎片 · deep-research 产物";  fragment.class: doc
    index:    "index.md\n库内索引";                    index.class: doc
  }

  raw -> wiki.sources: "结构化摘要" { style.stroke: "#059669"; style.stroke-width: 3 }
  wiki.sources  -> wiki.index: "登记条目"     { style.stroke-dash: 2 }
  wiki.fragment -> wiki.index: "登记 fragment" { style.stroke-dash: 2 }
}

others: "其它 libraries（结构同构：raw/ + wiki/）" {
  style: { fill: "#F9FAFB"; stroke: "#9CA3AF"; stroke-dash: 4 }
  grid-columns: 3
  exciteWiki; ickWiki; improveWiki; investWiki; opcWiki; quantitiveWiki
}

governance -> commands: "约束行为" { style.stroke: "#9333EA" }

commands.ingest         -> lib.raw:  "读原料→产出 wiki（先问压缩等级）" { style.stroke-dash: 3; style.stroke: "#0284C7" }
commands.query          -> lib.wiki: "检索 sources+fragment+index"      { style.stroke-dash: 3; style.stroke: "#0284C7" }
commands.raw_query      -> lib.raw:  "直查原文"                          { style.stroke-dash: 3; style.stroke: "#0284C7" }
commands.lint           -> lib.wiki: "lint/batch_lint：一致性+链接检查"  { style.stroke-dash: 3; style.stroke: "#0284C7" }
commands.check_uningest -> lib.raw:  "扫 raw 找未收录"                   { style.stroke-dash: 3; style.stroke: "#0284C7" }

commands -> others: "每个 library 同样适用" { style.stroke-dash: 4; style.stroke: "#9CA3AF" }
```

## 图怎么读（一句话）

`raw/`(黄色圆柱=原始资料) 经 **ingest** 变成 `wiki/sources`(结构化摘要)并登记进 `index.md`;
顶部 **CLAUDE.md/AGENT.md** 约束角色与边界,中间 **6 个命令**各自读写不同部分;
`aiagentWiki` 是展开示例,其余 6 个库结构完全相同。
