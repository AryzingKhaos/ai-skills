#!/usr/bin/env python3
"""Shared weasyprint helpers for the nutstore-book skill.

Why this file exists
--------------------
On macOS, weasyprint loads pango / cairo / glib / harfbuzz through dlopen, but it
asks for sonames like ``libgobject-2.0-0`` while Homebrew ships
``libgobject-2.0.0.dylib`` (a ``.0`` vs ``-0`` mismatch). With a stock brew the
import fails:

    OSError: cannot load library 'libgobject-2.0-0'

The fix is a shim directory of symlinks (``libgobject-2.0-0`` -> the real
``libgobject-2.0.0.dylib``) placed first on ``DYLD_FALLBACK_LIBRARY_PATH``.
Because dyld only reads ``DYLD_*`` at process start, the env must be set *before*
the interpreter launches — see ``ensure_dyld_or_reexec`` which re-execs once.

Why weasyprint at all (and never Chrome headless): on macOS, Chrome --headless
silently renders CJK glyphs blank while pdfinfo still reports normal pages.
weasyprint goes through Pango + fontconfig, which does per-glyph CJK fallback
against system fonts (PingFang / Hiragino / Songti …). Verified 2026-06.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys

CACHE_LIB = pathlib.Path.home() / ".cache" / "nutstore-book" / "lib"
BREW_LIBDIRS = ["/opt/homebrew/lib", "/usr/local/lib"]

# CJK-safe base style. Sets an A4 page and a Chinese-capable default font stack.
# Injected as an author stylesheet AFTER the document's own CSS, so a more
# specific rule (e.g. textutil's `p.p1 { font-family: Helvetica }`) still wins
# for Latin text while Pango falls back per-glyph to these fonts for any CJK.
CJK_FALLBACK_CSS = """
@page { size: A4; margin: 20mm 16mm; }
html, body {
  font-family: "PingFang SC","Hiragino Sans GB","Songti SC","STSong",
               "Microsoft YaHei","Heiti SC","STHeiti", serif;
  line-height: 1.7; color: #1d1d1f;
}
body { max-width: 820px; margin: 0 auto; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; }
td, th { border: 1px solid #d0d0d6; padding: 4px 8px; }
a { color: #0a64c8; text-decoration: none; word-break: break-all; }
pre, code { font-family: "SF Mono","Menlo","PingFang SC","Hiragino Sans GB", monospace; }
"""


def build_shim() -> pathlib.Path:
    """Create symlinks fixing weasyprint's soname mismatch; return the shim dir.

    Maps ``libfoo-1.0.0.dylib`` -> a ``libfoo-1.0-0`` symlink. Idempotent.
    """
    CACHE_LIB.mkdir(parents=True, exist_ok=True)
    pat = re.compile(r"^(lib.+?)-(\d+\.\d+)\.0\.dylib$")  # libpango-1.0.0.dylib
    for d in BREW_LIBDIRS:
        dp = pathlib.Path(d)
        if not dp.is_dir():
            continue
        for f in dp.glob("lib*.dylib"):
            m = pat.match(f.name)
            if not m:
                continue
            alt = f"{m.group(1)}-{m.group(2)}-0"  # libpango-1.0-0
            link = CACHE_LIB / alt
            try:
                if link.is_symlink() or link.exists():
                    if not (link.is_symlink() and os.readlink(link) == str(f)):
                        link.unlink()
                        link.symlink_to(f)
                else:
                    link.symlink_to(f)
            except OSError:
                pass
    return CACHE_LIB


def _dyld_value() -> str:
    shim = str(build_shim())
    parts = [shim] + [d for d in BREW_LIBDIRS if pathlib.Path(d).is_dir()]
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if existing:
        parts.append(existing)
    return ":".join(parts)


def ensure_dyld_or_reexec() -> None:
    """Set DYLD_FALLBACK_LIBRARY_PATH (with the shim) and re-exec once.

    Must be called before any weasyprint import / subprocess. dyld only reads
    DYLD_* at process start, so we re-exec the interpreter with the corrected
    env, guarded by _NB_DYLD_READY to avoid looping. After this returns, both
    in-process weasyprint and any inherited subprocess (e.g. the md-to-pdf
    scripts) can load the native libs.
    """
    if sys.platform != "darwin":
        return
    if os.environ.get("_NB_DYLD_READY") == "1":
        return
    env = dict(os.environ)
    env["DYLD_FALLBACK_LIBRARY_PATH"] = _dyld_value()
    env["_NB_DYLD_READY"] = "1"
    os.execve(sys.executable, [sys.executable] + sys.argv, env)


def render_html_file_to_pdf(html_path: pathlib.Path, pdf_path: pathlib.Path,
                            extra_css: str = CJK_FALLBACK_CSS) -> None:
    """Render an on-disk HTML file (relative resources resolved from its dir)."""
    from weasyprint import HTML, CSS  # type: ignore
    HTML(filename=str(html_path)).write_pdf(
        str(pdf_path), stylesheets=[CSS(string=extra_css)]
    )


def render_html_string_to_pdf(html_str: str, pdf_path: pathlib.Path,
                              base_url: str | None = None,
                              extra_css: str = CJK_FALLBACK_CSS) -> None:
    from weasyprint import HTML, CSS  # type: ignore
    HTML(string=html_str, base_url=base_url).write_pdf(
        str(pdf_path), stylesheets=[CSS(string=extra_css)]
    )


def html_text_len(path: pathlib.Path) -> int:
    """Length of the rendered (visible) text — excludes <style>/<script> text,
    so the verify ratio isn't deflated by inlined CSS (e.g. textutil output)."""
    try:
        from lxml import html as lhtml, etree
        root = lhtml.parse(str(path)).getroot()
        etree.strip_elements(root, "style", "script", with_tail=False)
        return len(root.text_content())
    except Exception:
        return path.stat().st_size


def verify_text_layer(pdf_path: pathlib.Path, src_chars: int) -> None:
    """pdftotext sanity check: warn loudly if the body text didn't render."""
    if shutil.which("pdftotext") is None:
        print("     提示：pdftotext 未安装，自动验证跳过；请用 Read 工具看首/中/末页")
        return
    try:
        out = subprocess.run(["pdftotext", str(pdf_path), "-"],
                             capture_output=True, text=True, timeout=60)
        chars = len(out.stdout)
    except subprocess.TimeoutExpired:
        print("     提示：pdftotext 超时，请手动视觉抽查")
        return
    ratio = chars / max(src_chars, 1)
    print(f"     文字层 {chars} 字（源约 {src_chars} 字，比例 {ratio:.0%}）")
    if ratio < 0.10:
        print("     ⚠️ 比例 <10%，正文很可能未渲染——立即用 Read 工具排查！")
    elif ratio < 0.35:
        print("     提示：比例偏低，建议视觉抽查首/中/末页确认。")
