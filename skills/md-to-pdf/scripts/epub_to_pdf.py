#!/usr/bin/env python3
"""
epub_to_pdf.py — convert an EPUB to a PDF (CJK-safe).

Pipeline: unzip epub → read spine order from .opf → concat spine XHTML bodies
        → inline the epub's own CSS + inject a CJK font fallback
        → make image/link paths absolute → weasyprint → PDF.

Why weasyprint (not Chrome headless / calibre's Qt engine): on macOS, weasyprint
goes through Pango + fontconfig, which does per-glyph CJK font fallback against
system fonts (PingFang / Hiragino / Songti …). Chrome --headless renders CJK
blank while pdfinfo still reports normal pages. Verified pipeline 2026-06.

Deps: Python 3 + lxml + weasyprint. macOS also needs `brew install pango cairo glib`.
The script auto-injects DYLD_FALLBACK_LIBRARY_PATH so weasyprint finds them.

Usage:
  python3 epub_to_pdf.py path/to/book.epub                 # → same dir, .pdf
  python3 epub_to_pdf.py -i book.epub -o ~/out/book.pdf
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import unquote, urljoin

# CJK-safe base style. Kept low-touch: provides A4 page + a Chinese-capable
# default font stack; the epub's own CSS is inlined AFTER this so its layout
# wins, while Pango still falls back per-glyph for any missing CJK glyphs.
CJK_CSS = """
@page { size: A4; margin: 18mm 16mm; }
html, body {
  font-family: "PingFang SC","Hiragino Sans GB","Songti SC","STSong",
               "Microsoft YaHei","Heiti SC","STHeiti", serif;
  line-height: 1.7; color: #1d1d1f;
}
body { max-width: 800px; margin: 0 auto; }
img { max-width: 100%; height: auto; }
p { font-size: 11pt; }
h1 { font-size: 21pt; } h2 { font-size: 16pt; } h3 { font-size: 13.5pt; }
.epub-section { page-break-before: always; }
.epub-section:first-of-type { page-break-before: avoid; }
.epub-cover { page-break-after: always; text-align: center; margin: 0; }
.epub-cover img { max-width: 100%; max-height: 245mm; }
table { border-collapse: collapse; }
td, th { border: 1px solid #d0d0d6; padding: 4px 8px; }
a { color: #0a64c8; text-decoration: none; word-break: break-all; }
"""


def ensure_dyld_path() -> None:
    """macOS: inject brew lib path so weasyprint finds libgobject / pango / cairo."""
    if sys.platform != "darwin":
        return
    parts = [p for p in ("/opt/homebrew/lib", "/usr/local/lib") if pathlib.Path(p).is_dir()]
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if existing:
        parts.append(existing)
    if parts:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)


def _localname(tag) -> str:
    """Strip XML namespace from an lxml tag."""
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_SKIP_PREFIX = ("http://", "https://", "file://", "data:", "mailto:", "tel:",
                "#", "javascript:")


def absolutize_links(root, base_uri: str) -> None:
    """Rewrite every relative resource path to an absolute file:// URL.

    Covers HTML (img/@src, a/@href, link/@href, …) AND SVG <image xlink:href>,
    which lxml's built-in make_links_absolute does NOT handle — that gap is
    exactly why epub cover pages (SVG-wrapped images) render blank.
    """
    for el in root.iter():
        for attr in ("src", "href", "poster", _XLINK_HREF):
            val = el.get(attr)
            if not val:
                continue
            if val.strip().lower().startswith(_SKIP_PREFIX):
                continue
            el.set(attr, urljoin(base_uri, val))


def read_spine(workdir: pathlib.Path):
    """Return (content_root_dir, [xhtml_paths_in_reading_order])."""
    from lxml import etree

    container = workdir / "META-INF" / "container.xml"
    opf_rel = None
    if container.is_file():
        ctree = etree.parse(str(container))
        for rf in ctree.iter():
            if _localname(rf.tag) == "rootfile" and rf.get("full-path"):
                opf_rel = rf.get("full-path")
                break
    if not opf_rel:  # fallback: first .opf anywhere
        cands = list(workdir.rglob("*.opf"))
        if not cands:
            sys.exit("解析失败：epub 内找不到 .opf 清单文件")
        opf_rel = str(cands[0].relative_to(workdir))

    opf_path = workdir / opf_rel
    content_root = opf_path.parent
    otree = etree.parse(str(opf_path))

    manifest = {}   # id -> (href, media_type)
    for el in otree.iter():
        if _localname(el.tag) == "item":
            manifest[el.get("id")] = (el.get("href"), el.get("media-type") or "")

    spine = []
    for el in otree.iter():
        if _localname(el.tag) == "itemref":
            idref = el.get("idref")
            if idref in manifest:
                href, mt = manifest[idref]
                if href:
                    spine.append(href)

    if not spine:  # degenerate epub: take all xhtml in manifest order
        spine = [h for (h, mt) in manifest.values()
                 if h and (h.endswith((".xhtml", ".html", ".htm")) or "html" in mt)]

    # Cover IMAGE href: <meta name="cover" content="<manifest-id>"/>
    cover_img = None
    for el in otree.iter():
        if (_localname(el.tag) == "meta" and (el.get("name") or "").lower() == "cover"
                and el.get("content") in manifest):
            cover_img = manifest[el.get("content")][0]
            break
    # Cover PAGE href (the SVG/xhtml wrapper): guide <reference type="cover" href=...>
    cover_page = None
    for el in otree.iter():
        if (_localname(el.tag) == "reference" and (el.get("type") or "").lower() == "cover"
                and el.get("href")):
            cover_page = el.get("href").split("#")[0]
            break
    return content_root, spine, cover_img, cover_page


def build_combined_html(content_root: pathlib.Path, spine: list[str], title: str,
                        cover_img=None, cover_page=None) -> str:
    from lxml import html as lhtml
    from lxml import etree

    sections: list[str] = []
    inlined_css: list[str] = []
    seen_css: set[pathlib.Path] = set()

    # Cover: emit a plain <img> page (weasyprint renders raster <img> reliably,
    # but NOT external images inside inline SVG — which is how epub covers ship).
    if cover_img:
        cov = (content_root / unquote(cover_img)).resolve()
        if cov.is_file():
            sections.append(f'<div class="epub-cover"><img src="{cov.as_uri()}"/></div>')
    cover_page_norm = unquote(cover_page).split("#")[0] if cover_page else None

    for href in spine:
        href = unquote(href)
        # Skip the original cover wrapper page (SVG) — replaced by the <img> page above.
        if cover_page_norm and href.split("#")[0] == cover_page_norm:
            continue
        fpath = (content_root / href).resolve()
        if not fpath.is_file():
            continue
        doc = lhtml.parse(str(fpath))
        root = doc.getroot()
        # Resolve all relative img/src/href/xlink:href → absolute file:// URLs.
        absolutize_links(root, fpath.parent.as_uri() + "/")

        # Collect the epub's own stylesheets (inline their content, deduped by path).
        for ln in root.iter():
            if _localname(ln.tag) == "link" and (ln.get("type") == "text/css"
                                                 or (ln.get("rel") or "").lower() == "stylesheet"):
                css_href = ln.get("href")
                if not css_href:
                    continue
                if css_href.startswith("file://"):
                    css_abs = pathlib.Path(unquote(css_href[7:]))
                else:
                    css_abs = (fpath.parent / unquote(css_href)).resolve()
                if css_abs.is_file() and css_abs not in seen_css:
                    seen_css.add(css_abs)
                    try:
                        inlined_css.append(css_abs.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
            elif _localname(ln.tag) == "style" and (ln.text or "").strip():
                inlined_css.append(ln.text)

        body = None
        for el in root.iter():
            if _localname(el.tag) == "body":
                body = el
                break
        if body is None:
            continue
        inner = body.text or ""
        for child in body:
            inner += etree.tostring(child, encoding="unicode", method="html")
        sections.append(f'<div class="epub-section">{inner}</div>')

    epub_style = "\n".join(inlined_css)
    body_html = "\n".join(sections)
    return (
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<title>{title}</title>'
        f'<style>{CJK_CSS}</style>'
        f'<style>{epub_style}</style>'
        f'</head><body>{body_html}</body></html>'
    )


def html_to_pdf(html_str: str, pdf_path: pathlib.Path) -> None:
    ensure_dyld_path()
    try:
        from weasyprint import HTML
    except OSError as e:
        sys.exit(
            "weasyprint 加载失败（找不到 pango / cairo 等系统库）。\n"
            "  macOS: brew install pango cairo glib\n"
            f"  原始错误：{e}"
        )
    except ImportError:
        sys.exit("weasyprint 未安装：pip install --user weasyprint")
    HTML(string=html_str).write_pdf(str(pdf_path))


def verify(pdf_path: pathlib.Path, src_chars: int) -> None:
    if shutil.which("pdftotext") is None:
        print("提示：pdftotext 未安装，自动验证跳过；请用 Read 工具看 PDF 首/中/末页")
        return
    try:
        out = subprocess.run(["pdftotext", str(pdf_path), "-"],
                             capture_output=True, text=True, timeout=60)
        chars = len(out.stdout)
    except subprocess.TimeoutExpired:
        print("提示：pdftotext 超时，请手动视觉抽查")
        return
    ratio = chars / max(src_chars, 1)
    print(f"     文字层 {chars} 字（源文本约 {src_chars} 字，比例 {ratio:.0%}）")
    if ratio < 0.10:
        print("⚠️ 比例 <10%，正文汉字很可能未渲染——立即用 Read 工具排查！")
    elif ratio < 0.35:
        print("提示：比例偏低，建议视觉抽查首/中/末页确认。")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EPUB → PDF via weasyprint (CJK-safe).")
    ap.add_argument("epub", nargs="?", help="input .epub path")
    ap.add_argument("-i", "--input", help="input .epub (alias of positional)")
    ap.add_argument("-o", "--out", help="output PDF (default: same dir, .pdf)")
    ap.add_argument("--title", help="HTML <title> (default: epub file stem)")
    args = ap.parse_args(argv)

    src = args.epub or args.input
    if not src:
        print("error: 缺少 epub 文件（位置参数或 -i）", file=sys.stderr)
        return 2
    epub_path = pathlib.Path(src).expanduser().resolve()
    if not epub_path.is_file():
        print(f"找不到文件：{epub_path}", file=sys.stderr)
        return 1

    pdf_path = (pathlib.Path(args.out).expanduser().resolve()
                if args.out else epub_path.with_suffix(".pdf"))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    title = args.title or epub_path.stem

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="epub2pdf_"))
    try:
        with zipfile.ZipFile(epub_path) as z:
            z.extractall(workdir)
        content_root, spine, cover_img, cover_page = read_spine(workdir)
        print(f"spine: {len(spine)} 个 XHTML 文档" + (f" · 封面图 {cover_img}" if cover_img else " · 无封面图"))
        html_str = build_combined_html(content_root, spine, title, cover_img, cover_page)

        # 源文本字数（去标签估算）用于校验比例
        from lxml import html as lhtml
        try:
            src_chars = len(lhtml.fromstring(html_str).text_content())
        except Exception:
            src_chars = len(html_str)

        html_to_pdf(html_str, pdf_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    size_kb = pdf_path.stat().st_size / 1024
    print(f"PDF: {pdf_path}")
    print(f"     {size_kb:.1f} KB")
    verify(pdf_path, src_chars)
    print("⚑ 完成判定第二步：用 Read 工具看 PDF 首/中/末页 3 张做视觉抽查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
