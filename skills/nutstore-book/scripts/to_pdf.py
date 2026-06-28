#!/usr/bin/env python3
"""nutstore-book: convert text/ebook files to PDF, defaulting output to the
Nutstore (坚果云) 书籍 sync folder so the result auto-uploads.

Supported inputs (routed by extension):
  md / markdown      -> reuse md-to-pdf skill's md_to_pdf.py
  epub               -> reuse md-to-pdf skill's epub_to_pdf.py
  txt / text / log   -> wrap + weasyprint
  html / htm / xhtml -> weasyprint (relative resources resolved from file dir)
  rtf / rtfd         -> textutil -> html -> weasyprint   (macOS native)
  doc / docx / odt   -> textutil -> html -> weasyprint   (macOS native)
  mobi / azw / azw3  -> calibre `ebook-convert` if present, else python `mobi`
  / prc / kf8 / azw4    package -> epub/html -> weasyprint

Everything that rasterizes goes through weasyprint + Pango so CJK never drops.
NEVER swap in Chrome headless: it renders CJK blank while pdfinfo looks fine.

Usage:
  python3 to_pdf.py book.epub                      # -> newest 书籍/<date>/ folder
  python3 to_pdf.py a.docx b.mobi c.txt            # batch, same default dir
  python3 to_pdf.py book.md -o ~/Desktop/out.pdf   # explicit file (single input)
  python3 to_pdf.py *.epub -o ~/Desktop/pdfs/      # explicit directory
"""
from __future__ import annotations

import argparse
import datetime
import html as htmlmod
import pathlib
import shutil
import subprocess
import sys
import tempfile

import _weasy

# md/epub are delegated to the existing md-to-pdf skill (per user: reuse it).
MD_SKILL = pathlib.Path("/Users/aaron/code/ai-skills/skills/md-to-pdf/scripts")
NUTSTORE_BOOK_ROOT = pathlib.Path(
    "/Users/aaron/Documents/坚果云根/我的坚果云/书籍"
)

ROUTES = {
    ".md": "md", ".markdown": "md", ".mdown": "md", ".mkd": "md",
    ".epub": "epub",
    ".txt": "txt", ".text": "txt", ".log": "txt",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".rtf": "textutil", ".rtfd": "textutil",
    ".doc": "textutil", ".docx": "textutil", ".odt": "textutil",
    ".wordml": "textutil",
    ".mobi": "ebook", ".azw": "ebook", ".azw3": "ebook", ".azw4": "ebook",
    ".prc": "ebook", ".kf8": "ebook",
}


# ---------------------------------------------------------------- output dir

def _parse_dateish(name: str):
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y_%m_%d", "%Y.%m.%d"):
        try:
            return datetime.datetime.strptime(name, fmt).date()
        except ValueError:
            continue
    return None


def default_out_dir() -> pathlib.Path:
    """Reuse the existing 书籍/ subfolder whose date is closest to today.

    Falls back to the most-recently-modified subfolder if none are date-named,
    and to a fresh today (yyyy-MM-dd) folder if 书籍/ is empty or absent.
    """
    today = datetime.date.today()
    if NUTSTORE_BOOK_ROOT.is_dir():
        dated = []
        plain = []
        for d in NUTSTORE_BOOK_ROOT.iterdir():
            if not d.is_dir():
                continue
            dt = _parse_dateish(d.name)
            if dt is not None:
                dated.append((abs((dt - today).days), d))
            else:
                plain.append(d)
        if dated:
            dated.sort(key=lambda t: t[0])
            return dated[0][1]
        if plain:
            plain.sort(key=lambda p: p.stat().st_mtime)
            return plain[-1]
    return NUTSTORE_BOOK_ROOT / today.strftime("%Y-%m-%d")


def resolve_output(inp: pathlib.Path, out_arg: str | None, multi: bool) -> pathlib.Path:
    if out_arg:
        op = pathlib.Path(out_arg).expanduser()
        treat_as_file = op.suffix.lower() == ".pdf" and not op.is_dir() and not multi
        if treat_as_file:
            op.parent.mkdir(parents=True, exist_ok=True)
            return op
        op.mkdir(parents=True, exist_ok=True)
        return op / (inp.stem + ".pdf")
    d = default_out_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / (inp.stem + ".pdf")


# ---------------------------------------------------------------- converters

def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def via_md_skill(script: str, inp: pathlib.Path, outp: pathlib.Path) -> None:
    """Delegate to md-to-pdf (env already carries the weasyprint DYLD shim)."""
    _run([sys.executable, str(MD_SKILL / script), "-i", str(inp), "-o", str(outp)])


def convert_txt(inp: pathlib.Path, outp: pathlib.Path) -> None:
    text = inp.read_text(encoding="utf-8", errors="replace")
    esc = htmlmod.escape(text)
    html_str = (
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<title>{htmlmod.escape(inp.stem)}</title></head>'
        f'<body><pre class="txt">{esc}</pre></body></html>'
    )
    css = _weasy.CJK_FALLBACK_CSS + (
        "pre.txt{white-space:pre-wrap;word-break:break-word;"
        "font-family:inherit;font-size:11.5pt;line-height:1.7;margin:0;}"
    )
    _weasy.render_html_string_to_pdf(html_str, outp, extra_css=css)
    _weasy.verify_text_layer(outp, len(text))


def convert_html(inp: pathlib.Path, outp: pathlib.Path) -> None:
    _weasy.render_html_file_to_pdf(inp, outp)
    _weasy.verify_text_layer(outp, _weasy.html_text_len(inp))


def convert_via_textutil(inp: pathlib.Path, outp: pathlib.Path) -> None:
    if shutil.which("textutil") is None:
        sys.exit("此格式需要 macOS 自带的 textutil（未找到）。")
    with tempfile.TemporaryDirectory(prefix="nb_tu_") as td:
        htmlf = pathlib.Path(td) / "conv.html"
        _run(["textutil", "-convert", "html", "-encoding", "UTF-8",
              str(inp), "-output", str(htmlf)])
        _weasy.render_html_file_to_pdf(htmlf, outp)
        src = _weasy.html_text_len(htmlf)
    _weasy.verify_text_layer(outp, src)


def convert_ebook(inp: pathlib.Path, outp: pathlib.Path) -> None:
    """mobi / azw / azw3 -> epub or html -> PDF.

    Prefers calibre's `ebook-convert` (best fidelity). Falls back to the pure
    -Python `mobi` package, which unpacks to an epub (KF8/azw3) or html (old mobi).
    """
    ebook_convert = shutil.which("ebook-convert")
    with tempfile.TemporaryDirectory(prefix="nb_eb_") as td:
        tdp = pathlib.Path(td)
        if ebook_convert:
            epub = tdp / (inp.stem + ".epub")
            _run([ebook_convert, str(inp), str(epub)])
            via_md_skill("epub_to_pdf.py", epub, outp)
            return
        try:
            import mobi  # type: ignore
        except ImportError:
            sys.exit(
                "mobi / azw3 需要一个转换器，二选一安装：\n"
                "  pip install --user mobi        # 纯 Python，轻量\n"
                "  brew install --cask calibre    # 转换质量最好\n"
                "装好任一即可重跑。"
            )
        extract_dir, main_file = mobi.extract(str(inp))
        try:
            mf = pathlib.Path(main_file)
            suf = mf.suffix.lower()
            if suf == ".epub":
                via_md_skill("epub_to_pdf.py", mf, outp)
            elif suf in (".html", ".htm", ".xhtml"):
                convert_html(mf, outp)
            else:
                epubs = sorted(pathlib.Path(extract_dir).rglob("*.epub"))
                if epubs:
                    via_md_skill("epub_to_pdf.py", epubs[0], outp)
                else:
                    htmls = sorted(pathlib.Path(extract_dir).rglob("*.html"))
                    if not htmls:
                        sys.exit("mobi 解包后未找到 epub/html，无法转换。")
                    convert_html(htmls[0], outp)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)


HANDLERS = {
    "md": lambda i, o: via_md_skill("md_to_pdf.py", i, o),
    "epub": lambda i, o: via_md_skill("epub_to_pdf.py", i, o),
    "txt": convert_txt,
    "html": convert_html,
    "textutil": convert_via_textutil,
    "ebook": convert_ebook,
}


# ---------------------------------------------------------------- main

def convert_one(inp: pathlib.Path, out_arg: str | None, multi: bool) -> bool:
    kind = ROUTES.get(inp.suffix.lower())
    if kind is None:
        print(f"✗ {inp.name}: 不支持的格式 {inp.suffix}（支持 "
              f"{', '.join(sorted({e for e in ROUTES}))}）", file=sys.stderr)
        return False
    outp = resolve_output(inp, out_arg, multi)
    print(f"→ {inp.name}  [{kind}]")
    try:
        HANDLERS[kind](inp, outp)
    except subprocess.CalledProcessError as e:
        print(f"✗ {inp.name}: 子进程失败（{e.returncode}）", file=sys.stderr)
        return False
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - report and continue the batch
        print(f"✗ {inp.name}: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    if outp.exists():
        print(f"✓ {outp}  ({outp.stat().st_size/1024:.1f} KB)")
        return True
    print(f"✗ {inp.name}: 未生成 PDF", file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="to_pdf",
        description="把 md/txt/html/rtf/doc(x)/epub/mobi/azw3 转成 PDF，"
                    "默认落到坚果云书籍目录（自动上传）。",
    )
    ap.add_argument("inputs", nargs="+", help="一个或多个输入文件")
    ap.add_argument("-o", "--out",
                    help="输出路径：单文件可给 .pdf 文件名；给目录则按原名输出；"
                         "省略则用坚果云书籍目录里日期最接近今天的文件夹")
    args = ap.parse_args(argv)

    # Fix weasyprint's native-lib loading before anything imports it / subprocesses.
    _weasy.ensure_dyld_or_reexec()

    paths = [pathlib.Path(p).expanduser().resolve() for p in args.inputs]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"找不到文件：{p}", file=sys.stderr)
        return 1

    multi = len(paths) > 1
    ok = 0
    for p in paths:
        if convert_one(p, args.out, multi):
            ok += 1

    print(f"\n完成 {ok}/{len(paths)}。")
    print("⚑ 完成判定：用 Read 工具看每个 PDF 的首/中/末页 3 张做视觉抽查，"
          "确认中文确实渲染（别只看文件大小）。")
    return 0 if ok == len(paths) else 1


if __name__ == "__main__":
    raise SystemExit(main())
