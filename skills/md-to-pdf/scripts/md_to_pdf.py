#!/usr/bin/env python3
"""
md_to_pdf.py — convert a Markdown file to a PDF.

Pipeline: markdown → HTML (tables / fenced_code / toc / sane_lists / attr_list)
        → inject CSS → weasyprint → PDF.

On macOS, automatically injects DYLD_FALLBACK_LIBRARY_PATH so weasyprint
can find brew-installed pango / cairo / glib.

Why not Chrome headless: on macOS, Chrome --headless (and --headless=new)
cannot access system CJK fonts. The resulting PDF looks structurally correct
(tables, bullets, dividers) but the body text is silently rendered blank,
while pdfinfo still reports correct page counts. Verified failure mode 2026-05-30.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

DEFAULT_CSS = """
  @page { size: A4; margin: 22mm 18mm 22mm 18mm; }
  html, body { font-family: "PingFang SC","Hiragino Sans GB","Microsoft YaHei","Heiti SC","STHeiti",sans-serif;
               font-size: 11.5pt; line-height: 1.7; color: #1d1d1f; }
  body { max-width: 760px; margin: 0 auto; padding: 0 6px; }
  h1 { font-size: 22pt; border-bottom: 2px solid #1d1d1f; padding-bottom: 8px; margin-top: 18px; }
  h2 { font-size: 16.5pt; margin-top: 28px; border-bottom: 1px solid #d0d0d6; padding-bottom: 6px; page-break-after: avoid; }
  h3 { font-size: 13.5pt; margin-top: 18px; page-break-after: avoid; }
  h4 { font-size: 12pt; margin-top: 12px; }
  p,li { font-size: 11pt; }
  blockquote { border-left: 3px solid #888; padding: 6px 14px; margin: 10px 0;
               background: #f6f6f8; color: #333; font-style: normal; }
  blockquote p { margin: 4px 0; }
  code { font-family: "SF Mono","Menlo","PingFang SC","Hiragino Sans GB",monospace; background: #f0f0f5; padding: 1px 4px; border-radius: 3px; font-size: 10pt; }
  pre  { font-family: "SF Mono","Menlo","PingFang SC","Hiragino Sans GB",monospace; background: #f6f6f8; padding: 10px 12px; border-radius: 4px; overflow: auto; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 10.5pt; }
  th, td { border: 1px solid #d0d0d6; padding: 6px 10px; text-align: left; vertical-align: top; }
  th { background: #f0f0f5; font-weight: 600; }
  hr { border: 0; border-top: 1px solid #d0d0d6; margin: 22px 0; }
  a { color: #0a64c8; text-decoration: none; word-break: break-all; }
  strong { color: #000; }
  ul, ol { padding-left: 1.6em; }
  li > p { margin: 4px 0; }
  table, blockquote, pre, h2, h3 { page-break-inside: avoid; }
"""


def ensure_dyld_path() -> None:
    """On macOS, inject brew lib path so weasyprint can find libgobject / pango / cairo."""
    if sys.platform != "darwin":
        return
    candidates = ["/opt/homebrew/lib", "/usr/local/lib"]
    parts = [p for p in candidates if pathlib.Path(p).is_dir()]
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if existing:
        parts.append(existing)
    if parts:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)


def md_to_html(md_text: str, css: str, title: str) -> str:
    try:
        import markdown  # type: ignore
    except ImportError:
        sys.exit(
            "Python 'markdown' 包未安装。\n"
            "  pip install --user markdown"
        )
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
    )
    return (
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<title>{title}</title><style>{css}</style></head>'
        f'<body>{body}</body></html>'
    )


def html_to_pdf(html_path: pathlib.Path, pdf_path: pathlib.Path) -> None:
    ensure_dyld_path()
    try:
        from weasyprint import HTML  # type: ignore
    except OSError as e:
        sys.exit(
            "weasyprint 加载失败（找不到 pango / cairo 等系统库）。\n"
            "  macOS 安装：brew install pango cairo glib\n"
            f"  原始错误：{e}"
        )
    except ImportError:
        sys.exit(
            "weasyprint 未安装。\n"
            "  pip install --user weasyprint\n"
            "  macOS 还需要：brew install pango cairo glib"
        )
    HTML(filename=str(html_path)).write_pdf(str(pdf_path))


def verify_text_layer(pdf_path: pathlib.Path, md_chars: int) -> tuple[int, str]:
    """Use pdftotext to extract characters; return (count, warning_message)."""
    if shutil.which("pdftotext") is None:
        return (
            -1,
            "提示：pdftotext 未安装，自动验证跳过。请手动用 Read 工具看 PDF 首/中/末页确认内容",
        )
    try:
        out = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return -1, "提示：pdftotext 抽取超时，请手动验证"
    chars = len(out.stdout)
    ratio = chars / max(md_chars, 1)
    if ratio < 0.10:
        warn = (
            f"⚠️ pdftotext 只抽出 {chars} 字（源 {md_chars} 字，比例 {ratio:.1%}）。\n"
            f"   PDF 很可能正文汉字未渲染——立即用 Read 工具看实际页面排查。\n"
            f"   常见原因：用了 Chrome headless 而不是 weasyprint。"
        )
    elif ratio < 0.35:
        warn = (
            f"提示：pdftotext 抽出 {chars} 字（源 {md_chars} 字，比例 {ratio:.1%}）。\n"
            f"   比例偏低但可能正常，仍建议视觉抽查首/末页。"
        )
    else:
        warn = ""
    return chars, warn


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="md_to_pdf",
        description="Convert Markdown → PDF via weasyprint (CJK-safe, A4, system fonts).",
    )
    ap.add_argument("md", nargs="?", help="input markdown file path")
    ap.add_argument("-i", "--input", help="input markdown file (alias of positional)")
    ap.add_argument(
        "-o",
        "--out",
        help="output PDF path (default: same dir as input, .pdf extension)",
    )
    ap.add_argument("--css", help="path to custom CSS file (overrides built-in style)")
    ap.add_argument(
        "--title",
        help="HTML <title> for the document (default: input file stem)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    md_path_str = args.md or args.input
    if not md_path_str:
        print(
            "error: missing markdown file (pass as positional or -i / --input)",
            file=sys.stderr,
        )
        return 2

    md_path = pathlib.Path(md_path_str).expanduser().resolve()
    if not md_path.is_file():
        print(f"找不到文件：{md_path}", file=sys.stderr)
        return 1

    if args.out:
        pdf_path = pathlib.Path(args.out).expanduser().resolve()
    else:
        pdf_path = md_path.with_suffix(".pdf")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    md_text = md_path.read_text(encoding="utf-8")
    md_chars = len(md_text)

    if args.css:
        css_path = pathlib.Path(args.css).expanduser().resolve()
        if not css_path.is_file():
            print(f"找不到 CSS 文件：{css_path}", file=sys.stderr)
            return 1
        css = css_path.read_text(encoding="utf-8")
    else:
        css = DEFAULT_CSS

    title = args.title or md_path.stem
    html = md_to_html(md_text, css, title)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", encoding="utf-8", delete=False
    ) as f:
        f.write(html)
        html_tmp = pathlib.Path(f.name)

    try:
        html_to_pdf(html_tmp, pdf_path)
    finally:
        try:
            html_tmp.unlink()
        except OSError:
            pass

    chars, warn = verify_text_layer(pdf_path, md_chars)
    size_kb = pdf_path.stat().st_size / 1024
    chars_str = str(chars) if chars >= 0 else "未知"
    print(f"PDF: {pdf_path}")
    print(f"     {size_kb:.1f} KB · 文字层 {chars_str} 字（源 {md_chars}）")
    if warn:
        print(warn)
    # 提醒上层调用者完成判定的第二步
    print("⚑ 完成判定第二步：用 Read 工具看 PDF 的首/中/末页 3 张做视觉抽查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
