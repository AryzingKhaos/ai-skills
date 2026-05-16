#!/usr/bin/env python3
"""
端到端发布脚本：md 文件 → 微信公众号草稿箱。

用法：
  python3 publish.py \
    --config /path/to/.wechat-config.json \
    --md /path/to/article.publish.md \
    [--cover /path/to/cover.jpg] \
    [--digest "摘要"] \
    [--author "作者"] \
    [--source-url https://...] \
    [--dry-run]

成功后打印草稿 media_id 与公众号后台草稿箱 URL。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保能 import 同目录下的模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from md_to_html import convert  # noqa: E402
from wechat_client import WeChatClient, WeChatConfig, WeChatError  # noqa: E402


DRAFT_CONSOLE_URL = (
    "https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_card&type=10&start=0&count=10"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="微信凭证 JSON 路径")
    parser.add_argument("--md", required=True, help="发布版 markdown 路径")
    parser.add_argument("--cover", help="封面图本地路径（建议传，发布时必需）")
    parser.add_argument("--digest", default="", help="摘要（54 字以内）")
    parser.add_argument("--author", default="", help="作者署名（覆盖 config 中的默认值）")
    parser.add_argument("--source-url", default="", help="原文链接")
    parser.add_argument(
        "--need-open-comment",
        type=int,
        default=1,
        choices=[0, 1],
        help="是否开启评论，1 开 0 关（默认 1）",
    )
    parser.add_argument(
        "--only-fans-can-comment",
        type=int,
        default=0,
        choices=[0, 1],
        help="是否仅粉丝可评论，1 是 0 否（默认 0）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只转换 HTML 并打印，不上传到微信",
    )
    args = parser.parse_args()

    md_path = Path(args.md).resolve()
    if not md_path.exists():
        print(f"错误：md 文件不存在 {md_path}", file=sys.stderr)
        return 2

    md_text = md_path.read_text(encoding="utf-8")

    if args.dry_run:
        client = None
        config = None
    else:
        try:
            config = WeChatConfig.from_file(args.config)
        except (FileNotFoundError, ValueError) as exc:
            print(f"错误：读取 config 失败 — {exc}", file=sys.stderr)
            return 2
        client = WeChatClient(config)

    print(f"→ 转换 markdown：{md_path}")
    try:
        title, html, warnings = convert(md_text, md_path.parent, client)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(f"  标题：{title}")
    print(f"  HTML 长度：{len(html)} 字符")
    for w in warnings:
        print(f"  ⚠ {w}")

    digest = args.digest.strip()
    if len(digest) > 54:
        print(f"  ⚠ 摘要超过 54 字（当前 {len(digest)}），微信会截断")

    author = args.author or (config.author if config else "")

    if args.dry_run:
        print("\n--- dry-run HTML (前 800 字) ---")
        print(html[:800])
        print("...\n")
        return 0

    assert client is not None  # for type checker

    thumb_media_id = ""
    if args.cover:
        cover_path = Path(args.cover).resolve()
        if not cover_path.exists():
            print(f"错误：封面图不存在 {cover_path}", file=sys.stderr)
            return 2
        print(f"→ 上传封面图：{cover_path.name}")
        try:
            thumb_media_id = client.upload_thumb(cover_path)
        except WeChatError as exc:
            print(f"错误：封面上传失败 — {exc}", file=sys.stderr)
            return 1
        print(f"  thumb_media_id = {thumb_media_id}")
    else:
        print("  ⚠ 未指定封面（--cover）。草稿可保存，但发布前必须在后台补封面。")

    article: dict[str, object] = {
        "title": title,
        "author": author,
        "digest": digest,
        "content": html,
        "content_source_url": args.source_url,
        "need_open_comment": args.need_open_comment,
        "only_fans_can_comment": args.only_fans_can_comment,
    }
    if thumb_media_id:
        article["thumb_media_id"] = thumb_media_id

    print("→ 新增草稿…")
    try:
        media_id = client.add_draft([article])
    except WeChatError as exc:
        print(f"错误：草稿上传失败 — {exc}", file=sys.stderr)
        return 1

    print("\n✓ 草稿已创建")
    print(f"  media_id  = {media_id}")
    print(f"  公众号后台：{DRAFT_CONSOLE_URL}")
    if not thumb_media_id:
        print("  下一步：去后台打开这篇草稿，补一张封面图，预览后即可发布。")
    else:
        print("  下一步：去后台预览这篇草稿，没问题就发布。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
