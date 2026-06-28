---
name: wechat-article-downloader
description: "把微信公众号文章（mp.weixin.qq.com/s/… 链接）连同图片下载/存档成本地 Markdown：正文转 Markdown、图片按原文位置就地下载并改成本地引用、附原始 HTML 存档与元信息（公众号 / 链接 / 日期）。默认存到 /Users/aaron/workspace/llmWikis/aiagentWiki/raw/，可指定其它目录（如其它 wiki 的 raw/）。**只有显式调用才能使用此 skill**，禁止模糊匹配触发——用户只是贴出公众号链接、或随口说『看看这篇』不算触发。显式调用形式：'/wechat-article-downloader <链接> [目标目录]'、'用 wechat-article-downloader 下载这篇公众号文章'、'调用 wechat-article-downloader 把这篇存到 xxWiki/raw'、'调用 wechat-article-downloader'。"
---

# 微信公众号文章下载器

把一篇微信公众号文章（含图片）下载、整理成本地 Markdown 存档，主要用于喂进各 wiki 的 `raw/` 目录。

## 触发方式（必须显式调用）

只有用户**点名本 skill**时才执行，例如：

- `/wechat-article-downloader https://mp.weixin.qq.com/s/XXXX`
- `/wechat-article-downloader https://mp.weixin.qq.com/s/XXXX /Users/aaron/workspace/llmWikis/opcWiki/raw`
- “用 wechat-article-downloader 下载这篇公众号文章”
- “调用 wechat-article-downloader 把这篇存到 improveWiki/raw”

**不要**因为用户只是贴了个 `mp.weixin.qq.com` 链接、或说“帮我下载这篇文章”就自动触发——必须有明确的 skill 名称或斜杠命令。

## 参数

- `<链接>`（必填）：形如 `https://mp.weixin.qq.com/s/XXXX` 的文章地址。
- `[目标目录]`（可选）：保存到哪个目录。**默认 `/Users/aaron/workspace/llmWikis/aiagentWiki/raw/`**。用户指定了别的（如 `opcWiki/raw`、`improveWiki/raw`）就用用户给的。

## 执行步骤

1. 运行脚本（脚本会自己抓取网页、下载图片、生成 Markdown）：

   ```bash
   python3 /Users/aaron/code/ai-skills/skills/wechat-article-downloader/scripts/wx_download.py "<链接>" "[目标目录]"
   ```

   不传第二个参数时即用默认目录。脚本结束会打印：标题、公众号、日期、图片成功/失败数、输出目录与 Markdown 路径。

2. **核对元信息**：读一下生成的 Markdown 头部。
   - 如果 `来源公众号` 显示“（未识别，请人工核对）”，或明显是占位符/广告名，就从 `原始页面.html` 里搜 `nickname="`、`nick_name` 把真实公众号名补上（这类页面元数据偶尔抓不准）。
   - 发布日期未识别时同理可人工补。

3. **核对图片**：脚本会逐张下载并按图片真实内容定扩展名（自动修正微信偶尔 `wx_fmt` 与实际格式不符的情况）。若有“失败”的图片，Markdown 里会留下 `![图N（下载失败）](原始URL)` 的占位，重试一次脚本或手动 curl 补下（带 `Referer: https://mp.weixin.qq.com/`）。

4. 向用户汇报：标题、来源、存放路径、图片数量。

## 产物结构

```
<目标目录>/<文章标题slug>/
├── <标题>.md       正文 Markdown；图片按原文位置内嵌，引用指向本地 images/；头部含公众号/链接/日期
├── images/          所有图片（img01、img02…，扩展名按真实内容判定）
└── 原始页面.html    原始网页存档，便于回查或补抓元信息
```

## 说明与边界

- **纯文字文章**：有些公众号文章正文没有任何插图（微信会把懒加载图的真实地址写进服务端 HTML，所以抓不到就是真没有），这时只会生成 Markdown，`images/` 为空，属正常。
- 正文里的脚本、音频/语音插件、空标题会被清理；正文结构（标题层级、段落）尽量保真。
- 本 skill 只负责“下载存档”。后续把文章 `ingest` 进 wiki，请按对应仓库 `script/commands/ingest.md` 的流程另行处理。
- 与 `wechat-publisher` 区分：那个是把 HTML 文章发到公众号草稿箱（发布方向），本 skill 是把公众号文章下载到本地（存档方向）。
