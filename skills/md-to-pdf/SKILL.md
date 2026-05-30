---
name: md-to-pdf
description: "把 markdown 文件渲染成排版规整的 PDF（A4、中文友好、表格 / 引用 / 代码块齐全）。走 weasyprint + Pango 管线，原生支持 PingFang / Hiragino 等系统中文字体，不掉字。触发场景：'把这个 md 转成 pdf'、'生成 pdf'、'/md-to-pdf <path>'、'帮我把 xxx.md 导出为 pdf'、'markdown 转 pdf'。"
---

# MD → PDF

把 markdown 文件转成 A4 PDF，中文字体原生渲染、表格 / 引用块 / 代码块都正常。

## 一行命令

```bash
python3 /Users/aaron/code/ai-skills/skills/md-to-pdf/scripts/md_to_pdf.py path/to/foo.md
```

默认输出到同目录、同名、扩展名 `.pdf`。指定输出位置：

```bash
python3 /Users/aaron/code/ai-skills/skills/md-to-pdf/scripts/md_to_pdf.py \
  -i path/to/foo.md -o ~/Desktop/foo.pdf
```

换主题用 `--css <自定义.css>` 覆盖默认样式。改 HTML title 用 `--title "xxx"`（默认取文件名）。

## 严禁踩的坑

**不要用 Chrome headless 做这件事。** Chrome（不论 `--headless` 还是 `--headless=new`）在 macOS 上拿不到系统中文字体（PingFang / Hiragino / Microsoft YaHei…），渲染出来的 PDF 看着有结构（表格线、bullet、分隔线齐全）但**正文汉字全是空白**，而且 `pdfinfo` 仍然报正常页数和文件大小——**光看元数据看不出问题**。

本 skill 走 **weasyprint + Pango + 系统字体管线**，CJK 全程没问题。这是 2026-05-30 实战翻车后的总结。

## 完成判定（必须做，别偷懒）

声明"完成"前两件事缺一不可：

1. **抽文本**：`pdftotext output.pdf - | wc -m`，字数应接近源 MD 的 50–80%；若 < 10% 说明字体掉光了，立即排查
2. **视觉抽查**：用 Read 工具看**首页 / 中段 / 末页** 3 张，确认中文确实渲染了

脚本本身已经做了第 1 步并会在比例偏低时告警，但**视觉抽查仍是必须**。这条规则的来由：第一次实现这件事用了 Chrome headless，PDF 写出来 70KB / 12 页元数据齐全，被用户当场抓包"你说完成之前先验证一下产出物"。

## 默认排版

- 页面：A4 / 22mm 边距 / line-height 1.7 / 正文 11.5pt
- 字体回退链：`PingFang SC → Hiragino Sans GB → Microsoft YaHei → Heiti SC → STHeiti → sans-serif`
- 标题：H1 22pt 下划线、H2 16.5pt 灰线、H3 13.5pt
- 引用块：浅灰底（#f6f6f8）+ 左灰条
- 表格：边线 #d0d0d6、表头浅灰底；表格 / 引用 / 代码块 / h2 / h3 都加了 `page-break-inside: avoid`
- 代码：`SF Mono / Menlo` 等宽、浅灰底、圆角，后接 PingFang / Hiragino CJK 回退（重要：等宽字体本身不带中文 glyph，自定义 CSS 时这条 fallback 链别丢）
- 链接：`#0a64c8` 无下划线、`word-break: break-all`（长 URL 不撑破排版）

## 依赖

- Python 3 + `markdown` 包（macOS 自带 Python 通常已有）
- `weasyprint`（缺则脚本提示 `pip install --user weasyprint`）
- macOS 还需要 brew 装 pango / cairo / glib：
  ```bash
  brew install pango cairo glib
  ```
  脚本会自动注入 `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`（M 芯片）或 `/usr/local/lib`（Intel），不需要你手动 export
- `pdftotext`（用于自动验证；缺则跳过验证但仍生成 PDF）

## 何时不该用这个 skill

- 要严格 Word 排版（页眉页脚、目录、章节编号）→ 用 docx skill
- 需要嵌入大量公式（LaTeX）→ pandoc + xelatex 更合适
- 只是想要 HTML 预览（不要 PDF）→ 直接 `python3 -m markdown` 就够了

## 自定义 CSS 示例

要做横向版、深色版、双栏版，准备一个 css 文件丢给 `--css`：

```css
/* dark.css */
@page { size: A4; margin: 18mm; }
html, body { background: #1d1d1f; color: #f0f0f0;
             font-family: "PingFang SC", sans-serif; font-size: 11pt; }
h1, h2, h3 { color: #fff; border-color: #555; }
blockquote { background: #2a2a2e; border-color: #888; color: #ddd; }
table th { background: #2a2a2e; color: #fff; }
table th, table td { border-color: #444; }
a { color: #6cb2ff; }
```

调用：

```bash
python3 /Users/aaron/code/ai-skills/skills/md-to-pdf/scripts/md_to_pdf.py \
  -i notes.md --css dark.css
```
