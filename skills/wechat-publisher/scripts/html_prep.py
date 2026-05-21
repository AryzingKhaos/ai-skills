"""
HTML "上微信前清洗" 模块。

只做三件事：
  1. 提取标题（<title> 或第一个 <h1>）
  2. 处理 <img>：本地路径 → 上传到微信 → 替换为 CDN URL
  3. 剥掉微信会拒收/拦截的元素：<script> / <iframe> / <link rel="stylesheet"> / <style>

不做"排版美化"——那是 web-artifacts-builder 的活。这里只保证 HTML 进得了 cgi-bin/draft/add。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wechat_client import WeChatClient


# 微信公众号 HTML 不接受 / 会剥掉的标签
INCOMPATIBLE_BLOCK_TAGS = ("script", "iframe", "noscript")
# <style> 会被剥但不会拒收；warn 一下
WARN_TAGS = ("style",)


def extract_title(html: str) -> str:
    """优先 <title>，回退第一个 <h1>。两者都没有就抛错。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title:
            return title

    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        # 去掉 h1 内部可能的标签
        title = re.sub(r"<[^>]+>", "", m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title

    raise ValueError("HTML 既没有 <title> 也没有 <h1>，无法提取标题")


def strip_incompatible(html: str) -> tuple[str, list[str]]:
    """剥掉微信不接受的元素。返回 (清洗后 html, 警告列表)。"""
    warnings: list[str] = []

    # 剥块级不兼容标签（包括内容）
    for tag in INCOMPATIBLE_BLOCK_TAGS:
        pattern = re.compile(
            rf"<{tag}\b[^>]*>.*?</{tag}>", re.IGNORECASE | re.DOTALL
        )
        count = len(pattern.findall(html))
        if count:
            html = pattern.sub("", html)
            warnings.append(f"剥掉了 {count} 个 <{tag}> 块（微信不接受）")
        # 自闭合的也清掉
        self_closing = re.compile(rf"<{tag}\b[^/>]*/>", re.IGNORECASE)
        if self_closing.search(html):
            html = self_closing.sub("", html)

    # 剥外部 stylesheet link
    link_pattern = re.compile(
        r'<link\b[^>]*rel\s*=\s*["\']?stylesheet["\']?[^>]*/?>',
        re.IGNORECASE,
    )
    link_count = len(link_pattern.findall(html))
    if link_count:
        html = link_pattern.sub("", html)
        warnings.append(
            f"剥掉了 {link_count} 个外部 stylesheet link（微信会拦截，请改成 inline style）"
        )

    # warn-only：<style> 块
    for tag in WARN_TAGS:
        if re.search(rf"<{tag}\b", html, re.IGNORECASE):
            warnings.append(
                f"HTML 含 <{tag}> 块——微信会剥掉，样式可能丢失。建议在 web-artifacts-builder 输出时全部 inline。"
            )

    return html, warnings


def process_images(
    html: str, html_dir: Path, client: "WeChatClient | None"
) -> tuple[str, list[str]]:
    """
    扫 <img src=...>：
      - 本地相对路径 → 拼绝对 → 上传到微信 → 替换 URL
      - http/https 微信域 → 保留
      - 其他外链 → 保留但警告
    """
    warnings: list[str] = []
    if client is None:
        return html, warnings

    def _replace(match: re.Match[str]) -> str:
        full = match.group(0)
        # 取 src 值
        src_match = re.search(r'src\s*=\s*["\']([^"\']+)["\']', full, re.IGNORECASE)
        if not src_match:
            return full
        src = src_match.group(1)

        if src.startswith(("http://", "https://")):
            if "mmbiz.qpic.cn" not in src and "wx.qlogo.cn" not in src:
                warnings.append(f"外链图片 {src} 在发布时可能被拦截，建议改成本地图")
            return full

        if src.startswith("data:"):
            warnings.append("发现 data: URI 图片，微信不支持，建议改成本地文件路径")
            return full

        image_path = (
            (html_dir / src).resolve() if not Path(src).is_absolute() else Path(src)
        )
        if not image_path.exists():
            warnings.append(f"图片不存在：{image_path}（保留原引用）")
            return full

        try:
            new_url = client.upload_content_image(image_path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"上传 {image_path.name} 失败：{exc}（保留原引用）")
            return full

        return re.sub(
            r'src\s*=\s*["\']([^"\']+)["\']',
            f'src="{new_url}"',
            full,
            count=1,
            flags=re.IGNORECASE,
        )

    new_html = re.sub(r"<img\b[^>]*>", _replace, html, flags=re.IGNORECASE)
    return new_html, warnings


def prepare(
    html: str,
    html_dir: Path,
    client: "WeChatClient | None" = None,
) -> tuple[str, str, list[str]]:
    """
    入口：把一段 HTML 处理成可直接喂给 cgi-bin/draft/add 的状态。

    返回 (title, cleaned_html, warnings)。
    """
    title = extract_title(html)
    cleaned, w1 = strip_incompatible(html)
    cleaned, w2 = process_images(cleaned, html_dir, client)
    return title, cleaned, w1 + w2
