#!/usr/bin/env python3
"""把微信公众号文章连同图片下载/存档成本地 Markdown。

用法:
    python3 wx_download.py <文章链接> [目标目录]

- <文章链接>: 形如 https://mp.weixin.qq.com/s/XXXX 的公众号文章地址
- [目标目录]: 可选，默认 /Users/aaron/workspace/llmWikis/aiagentWiki/raw/

产物（在 目标目录/<文章标题slug>/ 下）:
    <标题>.md      正文 Markdown，图片按原文位置内嵌，引用指向本地 images/
    images/        所有图片（按内容真实格式定扩展名）
    原始页面.html  原始网页存档
"""
import re, os, sys, html as ihtml, datetime, urllib.request

DEFAULT_DIR = "/Users/aaron/workspace/llmWikis/aiagentWiki/raw/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15")


def fetch(url, referer=None, binary=False):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    data = urllib.request.urlopen(req, timeout=40).read()
    return data if binary else data.decode("utf-8", "replace")


def meta(doc, prop, attr="property"):
    m = re.search(r'<meta %s="%s" content="([^"]*)"' % (attr, re.escape(prop)), doc)
    return ihtml.unescape(m.group(1)) if m else None


def find_nickname(doc):
    """尽量取到公众号名；跳过占位符/广告等明显错误值。"""
    bad = ("data-", "miniprogram", "nickname", "微信广告", "")
    pats = [
        r'<[^>]+\bnickname="([^"]{1,40})"',      # HTML 属性（最可靠）
        r'var nickname\s*=\s*"([^"]{1,40})"',
        r'"nick_name"\s*:\s*"([^"]{1,40})"',
        r"nick_name\s*:\s*'([^']{1,40})'",
    ]
    for pat in pats:
        for m in re.finditer(pat, doc):
            v = m.group(1).strip()
            if v and not any(b and b in v for b in bad):
                return v
    return None


def find_date(doc):
    for pat in [r'var ct\s*=\s*"(\d+)"', r'var publish_time\s*=\s*"(\d+)"',
                r'"create_time"\s*:\s*"?(\d{10})']:
        m = re.search(pat, doc)
        if m:
            return datetime.datetime.utcfromtimestamp(int(m.group(1))).strftime("%Y-%m-%d")
    return ""


def slugify(title):
    s = re.sub(r'[\\/:*?"<>|\t\r\n]+', "", title).strip()
    s = re.sub(r"\s+", "", s)
    return (s[:40] or "wechat_article")


def sniff_ext(data, fallback="png"):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return fallback


def convert(doc, url):
    og_title = meta(doc, "og:title") or "微信公众号文章"
    nick = find_nickname(doc)
    author = meta(doc, "og:article:author") or meta(doc, "author", "name")
    date_str = find_date(doc)

    start = doc.find('id="js_content"')
    if start == -1:
        raise SystemExit("未找到正文容器 js_content —— 链接可能失效或被反爬拦截。")
    start = doc.find(">", start) + 1
    ends = [doc.find(x, start) for x in ('id="content_bottom_area"', 'id="js_temp_bottom_area"')]
    ends = [e for e in ends if e != -1]
    end = min(ends) if ends else start + 500000
    body = doc[start:end]

    # 去掉脚本 / 音频 / 语音插件（含其内部文本）
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    body = re.sub(r"<mp-common-mpaudio.*?</mp-common-mpaudio>", "", body, flags=re.S)
    body = re.sub(r"<mpvoice[^>]*>.*?</mpvoice>", "", body, flags=re.S)

    # 抽取图片为占位符
    img_urls = []
    def repl_img(mt):
        tag = mt.group(0)
        ds = re.search(r'data-src="([^"]*)"', tag)
        sc = re.search(r'(?<!data-)\bsrc="([^"]*)"', tag)
        u = ""
        if ds and ds.group(1).strip():
            u = ds.group(1).strip()
        elif sc and sc.group(1).strip():
            u = sc.group(1).strip()
        if not u or not u.startswith("http") or "qpic" not in u:
            return ""
        img_urls.append(u)
        return f"\n\n@@IMG{len(img_urls)}@@\n\n"
    body = re.sub(r"<img[^>]*>", repl_img, body)

    # 标题 / 块级元素 → Markdown
    body = re.sub(r"<h1[^>]*>", "\n\n## ", body);  body = re.sub(r"</h1>", "\n", body)
    body = re.sub(r"<h2[^>]*>", "\n\n### ", body); body = re.sub(r"</h2>", "\n", body)
    body = re.sub(r"<h3[^>]*>", "\n\n#### ", body); body = re.sub(r"</h3>", "\n", body)
    body = re.sub(r"<li[^>]*>", "\n- ", body);     body = re.sub(r"</li>", "\n", body)
    body = re.sub(r"<blockquote[^>]*>", "\n\n> ", body); body = re.sub(r"</blockquote>", "\n", body)
    body = re.sub(r"<(section|p|div)[^>]*>", "\n\n", body)
    body = re.sub(r"</(section|p|div)>", "\n", body)
    body = re.sub(r"<br\s*/?>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body)
    body = ihtml.unescape(body)
    body = "\n".join(l.strip() for l in body.split("\n"))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    # 去掉只有标题标记、没有标题文字的空行
    body = "\n".join(l for l in body.split("\n") if not re.fullmatch(r"#{1,6}\s*", l.strip()))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    for junk in ("预览时标签不可点",):
        i = body.find(junk)
        if i != -1:
            body = body[:i].rstrip()

    return og_title, nick, author, date_str, body, img_urls


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法: python3 wx_download.py <文章链接> [目标目录]")
    url = sys.argv[1].strip()
    target = (sys.argv[2].strip() if len(sys.argv) > 2 else DEFAULT_DIR).rstrip("/") + "/"

    doc = fetch(url)
    og_title, nick, author, date_str, body, img_urls = convert(doc, url)

    out_dir = os.path.join(target, slugify(og_title))
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

    # 下载图片，按真实内容定扩展名，回填引用
    ok, fail = 0, []
    for i, u in enumerate(img_urls, 1):
        try:
            data = fetch(u, referer="https://mp.weixin.qq.com/", binary=True)
            if len(data) < 100:
                raise ValueError("内容过小")
            ext = sniff_ext(data, "png")
            fn = f"img{i:02d}.{ext}"
            open(os.path.join(img_dir, fn), "wb").write(data)
            body = body.replace(f"@@IMG{i}@@", f"![图{i}](images/{fn})")
            ok += 1
        except Exception as e:
            body = body.replace(f"@@IMG{i}@@", f"![图{i}（下载失败）]({u})")
            fail.append((i, str(e)[:60]))

    src_line = f"> 来源公众号：{nick or '（未识别，请人工核对）'}"
    if author and author != nick:
        src_line += f"（作者：{author}）"
    header = (f"# {og_title}\n\n"
              f"{src_line}\n"
              f"> 原文链接：{url}\n"
              f"> 发布日期：{date_str or '（未识别）'}\n\n"
              f"---\n\n")

    md_path = os.path.join(out_dir, slugify(og_title) + ".md")
    open(md_path, "w", encoding="utf-8").write(header + body + "\n")
    open(os.path.join(out_dir, "原始页面.html"), "w", encoding="utf-8").write(doc)

    print("=== 下载完成 ===")
    print(f"标题: {og_title}")
    print(f"公众号: {nick or '（未识别）'}" + (f" / 作者: {author}" if author else ""))
    print(f"日期: {date_str or '（未识别）'}")
    print(f"图片: 共 {len(img_urls)} 张，成功 {ok}，失败 {len(fail)}")
    for i, e in fail:
        print(f"  图{i} 失败: {e}")
    print(f"输出目录: {out_dir}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    main()
