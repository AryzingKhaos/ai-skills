"""
Markdown → 微信公众号兼容 HTML 转换。

- 使用 `markdown` 库做基础转换。
- 注入一组保守的 inline 样式，让公众号编辑器接收时不掉格式。
- 把正文里的本地图片路径上传到微信素材服务器，替换为微信 CDN URL。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import markdown

if TYPE_CHECKING:
    from wechat_client import WeChatClient


# 公众号不支持 <style> 块，只能用 inline style。这里写一组保守的默认样式。
# 行内样式会通过简单的 tag → style 映射注入。
DEFAULT_STYLES: dict[str, str] = {
    "p": "margin: 1em 0; line-height: 1.75; color: #333; font-size: 16px;",
    "h1": "font-size: 22px; font-weight: bold; margin: 1.5em 0 0.8em; color: #222;",
    "h2": "font-size: 19px; font-weight: bold; margin: 1.4em 0 0.7em; color: #222; border-left: 4px solid #555; padding-left: 12px;",
    "h3": "font-size: 17px; font-weight: bold; margin: 1.3em 0 0.6em; color: #333;",
    "h4": "font-size: 16px; font-weight: bold; margin: 1.2em 0 0.5em; color: #333;",
    "ul": "margin: 1em 0; padding-left: 1.5em; line-height: 1.75;",
    "ol": "margin: 1em 0; padding-left: 1.5em; line-height: 1.75;",
    "li": "margin: 0.4em 0; color: #333; font-size: 16px;",
    "blockquote": "margin: 1em 0; padding: 0.6em 1em; border-left: 4px solid #ddd; background: #f8f8f8; color: #555; font-size: 15px;",
    "code": "background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: Menlo, Consolas, monospace; font-size: 14px;",
    "pre": "background: #2d2d2d; color: #f8f8f2; padding: 1em; border-radius: 4px; overflow-x: auto; font-family: Menlo, Consolas, monospace; font-size: 14px; line-height: 1.5;",
    "hr": "border: none; border-top: 1px solid #ddd; margin: 2em 0;",
    "strong": "font-weight: bold; color: #222;",
    "em": "font-style: italic;",
    "a": "color: #576b95; text-decoration: none;",
    "img": "max-width: 100%; height: auto; display: block; margin: 1em auto;",
}


def _inject_inline_styles(html: str) -> str:
    """把 DEFAULT_STYLES 注入到对应标签的 style 属性里。"""
    for tag, style in DEFAULT_STYLES.items():
        # 已有 style 的标签不动；只处理无 style 的开标签
        pattern = re.compile(
            rf"<{tag}(\s+[^>]*?)?>",
            flags=re.IGNORECASE,
        )

        def _replace(match: re.Match[str], _tag: str = tag, _style: str = style) -> str:
            existing_attrs = match.group(1) or ""
            if "style=" in existing_attrs.lower():
                return match.group(0)
            return f"<{_tag}{existing_attrs} style=\"{_style}\">"

        html = pattern.sub(_replace, html)
    return html


def _process_images(
    html: str, md_dir: Path, client: "WeChatClient | None"
) -> tuple[str, list[str]]:
    """
    扫 HTML 里的 <img src="...">：
      - 本地相对路径 → 拼成绝对路径 → 上传 → 替换 src 为微信 CDN URL
      - http/https 微信域 → 保留
      - 其他外链 → 保留但记录警告（公众号发布时可能被拦截）
    返回 (改写后的 html, 警告列表)。
    """
    warnings: list[str] = []
    if client is None:
        return html, warnings

    def _replace_src(match: re.Match[str]) -> str:
        full = match.group(0)
        src = match.group(1)

        if src.startswith(("http://", "https://")):
            if "mmbiz.qpic.cn" not in src and "wx.qlogo.cn" not in src:
                warnings.append(f"外链图片 {src} 在发布时可能被拦截，建议改成本地图。")
            return full

        # 本地路径
        image_path = (md_dir / src).resolve() if not Path(src).is_absolute() else Path(src)
        if not image_path.exists():
            warnings.append(f"图片不存在：{image_path}（保留原引用）")
            return full
        try:
            new_url = client.upload_content_image(image_path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"上传 {image_path.name} 失败：{exc}（保留原引用）")
            return full
        return full.replace(f'src="{src}"', f'src="{new_url}"').replace(
            f"src='{src}'", f'src="{new_url}"'
        )

    new_html = re.sub(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', _replace_src, html)
    return new_html, warnings


def convert(
    md_text: str,
    md_dir: Path,
    client: "WeChatClient | None" = None,
) -> tuple[str, str, list[str]]:
    """
    把 markdown 文本转成 (title, html_content, warnings)。

    title：第一行 H1。
    html_content：注入了内联样式、图片 URL 已替换为微信 CDN 的 HTML。
    """
    lines = md_text.splitlines()
    title = ""
    body_lines: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
            body_lines = lines[idx + 1 :]
            break
    if not title:
        raise ValueError("markdown 第一个 H1 缺失，无法提取标题")

    body_md = "\n".join(body_lines).lstrip()

    html = markdown.markdown(
        body_md,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html",
    )

    html, img_warnings = _process_images(html, md_dir, client)
    html = _inject_inline_styles(html)

    return title, html, img_warnings
