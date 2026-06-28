---
name: nutstore-book
description: "把各种文本/电子书格式（md、txt、html、rtf、doc/docx、epub、mobi、azw3 等）转成排版规整、中文不掉字的 A4 PDF，默认输出到坚果云『书籍』同步目录（自动上传）。走 weasyprint + Pango 管线，CJK 原生渲染。触发场景：'把这个 mobi/epub/docx 转成 pdf'、'转成 pdf 传到坚果云'、'/nutstore-book <file>'、'把这本书转 pdf 放书籍目录'、'批量转 pdf 上传坚果云'。md 转 pdf 复用 md-to-pdf skill。"
---

# nutstore-book —— 任意文本/电子书转 PDF，落到坚果云书籍目录

把 md / txt / html / rtf / doc / docx / epub / mobi / azw3 等转成 A4 PDF，
中文字体原生渲染，默认输出到坚果云『书籍』同步文件夹，传到那里会自动上传。

## 一行命令

```bash
python3 /Users/aaron/code/ai-skills/skills/nutstore-book/scripts/to_pdf.py <文件>
```

- 不给 `-o`：输出到 `…/坚果云根/我的坚果云/书籍/` 里**日期最接近今天的现有文件夹**；
  一个日期文件夹都没有时，才新建今天的 `yyyy-MM-dd/`。
- 批量：直接传多个文件，全部落到同一个默认目录。
  ```bash
  python3 …/to_pdf.py a.docx b.mobi c.epub
  ```
- 指定输出：单文件可给 `.pdf` 文件名；给目录则按原名输出。
  ```bash
  python3 …/to_pdf.py book.epub -o ~/Desktop/book.pdf
  python3 …/to_pdf.py *.epub      -o ~/Desktop/pdfs/
  ```

## 支持的格式与各自的管线

| 输入 | 怎么转 |
|---|---|
| `md` / `markdown` | **复用 md-to-pdf** 的 `md_to_pdf.py` |
| `epub` | **复用 md-to-pdf** 的 `epub_to_pdf.py`（处理 spine / 封面 / CJK） |
| `txt` / `text` / `log` | 包成 HTML（`pre-wrap` 自动折行）→ weasyprint |
| `html` / `htm` / `xhtml` | weasyprint 直接渲染，相对图片按文件目录解析 |
| `rtf` / `rtfd` | macOS 自带 `textutil` → html → weasyprint |
| `doc` / `docx` / `odt` | macOS 自带 `textutil` → html → weasyprint |
| `mobi` / `azw` / `azw3` / `prc` | calibre `ebook-convert`（若装了）否则 python `mobi` 包 → epub/html → PDF |

全部会走 weasyprint，所以中文都不掉字。

## 严禁踩的坑

**不要用 Chrome headless 做这件事。** Chrome（`--headless` / `--headless=new`）
在 macOS 上拿不到系统中文字体，渲染出的 PDF 结构看着齐全但**正文汉字全是空白**，
而 `pdfinfo` 仍报正常页数和大小——光看元数据看不出问题。本 skill 全程
**weasyprint + Pango + 系统字体**，CJK 没问题。（沿用 md-to-pdf 的实战教训。）

**默认目录是坚果云同步盘，会自动上传。** 自测/调试时务必用 `-o /tmp/...`，
别把测试垃圾写进 `书籍/`，否则会同步上云。

## 完成判定（必须做，别偷懒）

声明"完成"前两件事缺一不可：

1. **看脚本输出**：每个文件会打印 `文字层 N 字（比例 X%）`；比例 <10% 几乎肯定
   正文没渲染，立即排查。脚本会自动告警。
2. **视觉抽查**：用 Read 工具看每个 PDF 的**首页 / 中段 / 末页** 3 张，确认中文
   确实渲染了。这一步脚本替不了你。

来由见 md-to-pdf：第一次做这事用 Chrome headless，PDF 元数据齐全、正文却空白，
被当场抓包"说完成之前先验证产出物"。

## 依赖

- **md / epub**：依赖同机的 `md-to-pdf` skill（`/Users/aaron/code/ai-skills/skills/md-to-pdf`）。
- **weasyprint** + brew 的 `pango cairo glib`（`brew install pango cairo glib`）。
  - macOS 上 weasyprint 常因 soname 不匹配报 `cannot load library 'libgobject-2.0-0'`
    （brew 装的是 `libgobject-2.0.0.dylib`，`.0` vs `-0`）。本 skill 的 `_weasy.py`
    会在 `~/.cache/nutstore-book/lib/` 自动建好符号链接 shim，并在进程启动前注入
    `DYLD_FALLBACK_LIBRARY_PATH`（通过一次自我 re-exec），**无需手动处理**。
- **rtf / doc / docx**：macOS 自带 `textutil`，无需安装。
- **mobi / azw3**：装其一——
  - `pip install --user mobi`（纯 Python，轻量）
  - `brew install --cask calibre`（`ebook-convert`，转换质量最好；装了会优先用）
- `pdftotext`（来自 poppler，用于自动验证；缺了会跳过验证但仍生成 PDF）。
- `markdown` / `lxml` Python 包（md / epub / 校验用）。

## 默认目录怎么选的

`书籍/` 下的文件夹大多是 `yyyy-MM-dd`（如 `2020-03-09`）。默认行为是挑**日期距今天
最近**的那个文件夹放进去（复用现有归档），而不是每次新建。需要新文件夹时直接用
`-o …/书籍/2026-06-14/` 显式指定即可。

## 何时不该用这个 skill

- 只要把 md 转 pdf、不关心坚果云 → 直接用 `md-to-pdf` skill。
- 要严格 Word 排版（页眉页脚、目录、章节编号）→ 用 docx skill。
- 大量 LaTeX 公式 → pandoc + xelatex 更合适。
- 带 DRM 的 mobi/azw3 → 本 skill 不脱壳，转不了。
