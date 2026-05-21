---
name: wechat-publisher
description: >
  把已经排版好的 HTML 文章 + 封面图上传到微信公众号草稿箱。本 skill 只做"发布到草稿"这一件事；写稿请用 Viral_Writer_Skill，排版请用 web-artifacts-builder。
  触发场景：'上传到公众号草稿箱'、'发到公众号'、'/wechat-publisher 发 <文件>'、'把这个 HTML 推到微信草稿'。
  不触发：用户只是想写公众号内容（去 Viral_Writer_Skill）；只是想美化排版（去 web-artifacts-builder）。
---

# wechat-publisher 公众号草稿上传

**唯一职责**：拿一份已经排好版的 HTML + 一张封面图，上传到微信公众号草稿箱。

完整发稿流水线由三个 skill 串成，本 skill 是最后一棒：

```
Viral_Writer_Skill (写稿)
   → 产出 dist/<topic>-公众号.md
       → web-artifacts-builder (排版)
            → 产出 dist/<topic>.html
                → wechat-publisher (上传)  ← 本 skill
                     → 公众号后台草稿箱
```

## 工作目录约定

- 写作风格规范：`/Users/aaron/code/writerForSelfMedia/writingHabits/`
- 公众号内容定位：`/Users/aaron/code/writerForSelfMedia/wechat/公众号内容定位.md`
- 中间产物：`/Users/aaron/code/writerForSelfMedia/dist/`
  - 写稿产物：`<topic>-公众号.md`（来自 Viral_Writer_Skill）
  - 排版产物：`<topic>.html`（来自 web-artifacts-builder）
  - 封面图：`cover-<topic>.jpg|png`
- 微信凭证：`/Users/aaron/code/writerForSelfMedia/.wechat-config.json`（不入 git）
- 本 skill 的脚本：`/Users/aaron/code/ai-skills/skills/wechat-publisher/scripts/`

## 完整工作流（编排三个 skill）

### Step 1 — 写稿：调用 Viral_Writer_Skill

让 Viral_Writer_Skill 按 `wechat/公众号内容定位.md` + `writingHabits/公众号调性规范-资深从业者复盘.md` 写出 `dist/<topic>-公众号.md`。

写完，**用户确认 OK 后**，从该 md 抽出"纯发布部分"（H1 + 正文，去掉备选标题、配图指导、Sources 等附录），保存为 `dist/<topic>-公众号.publish.md`。

### Step 2 — 排版：调用 web-artifacts-builder

把 `<topic>-公众号.publish.md` 作为输入，让 web-artifacts-builder 产出**单文件、内联样式**的 HTML：

- 输出文件：`dist/<topic>.html`
- **必须满足**（微信公众号 HTML 限制）：
  - 只能有内联 style（不能有 `<style>` 块，不能有 `<link rel="stylesheet">`）
  - 不能有 `<script>` 标签
  - 不能用 React / Vue 之类的运行时；要纯静态 HTML
  - 字体不依赖外部 CDN（微信会去掉）
  - 图片用本地路径或微信域名（其他外链会被微信拦截或替换）

如果 web-artifacts-builder 默认生成的是带 Tailwind / React 的 artifact，要明确指示它"输出 WeChat-compatible 静态 HTML，所有样式 inline，不用 JS、不用外部 CSS"。

### Step 3 — 准备封面图

把 Viral_Writer_Skill 给的封面 prompt 让用户用 AI 画图工具（Midjourney / 即梦 / DALL-E 等）生成，放到 `dist/cover-<topic>.jpg`。

公众号草稿不带封面也能存，但发布前必须有。**建议每篇都备封面**。

### Step 4 — 上传草稿（本 skill 的本职工作）

用 Bash 调用：

```bash
python3 /Users/aaron/code/ai-skills/skills/wechat-publisher/scripts/publish.py \
  --config /Users/aaron/code/writerForSelfMedia/.wechat-config.json \
  --html /Users/aaron/code/writerForSelfMedia/dist/<topic>.html \
  --cover /Users/aaron/code/writerForSelfMedia/dist/cover-<topic>.jpg \
  --digest "<摘要 54 字内>" \
  --author "<作者名，可省略>"
```

参数说明：

- `--config`：微信凭证 JSON，含 `appid` / `appsecret`；照 `config.example.json` 抄一份并填值
- `--html`：排版好的 HTML 文件路径（来自 Step 2）
- `--cover`：封面图本地路径；省略则草稿不带封面（发布前在后台补）
- `--digest`：摘要，54 字内，公众号文章列表会显示
- `--author`：作者署名，可省

脚本会自动：

- 从 HTML 提取标题（优先 `<title>`，否则第一个 `<h1>`）
- 扫描 `<img src=>`：本地路径 → 上传到微信图文素材接口 → 替换为微信 CDN URL
- 剥掉微信不接受的元素：`<script>`、`<link rel="stylesheet">`、`<style>`（warn）、`<iframe>`
- 上传封面图为永久素材 → 拿到 `thumb_media_id`
- 调 `cgi-bin/draft/add` 新增草稿

成功返回草稿 `media_id` 并打印后台链接：
`https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_card&type=10&start=0&count=10`

让用户去后台审核 / 预览 / 发布。

## 调用本 skill 时的简化路径

如果用户**已经有一份排版好的 HTML**（不需要本 skill 协调前面两个 skill），直接走 Step 4。

例：用户说"把 `/path/to/article.html` 发到公众号草稿"——直接调 publish.py。

## 不要做的事

本 skill 不负责这些（去别的 skill）：

- ❌ 写文章正文 → 用 Viral_Writer_Skill
- ❌ 设计排版样式 → 用 web-artifacts-builder
- ❌ 生成封面图 → 让用户用图像生成工具
- ❌ 从 md 转 HTML → 用 web-artifacts-builder（旧版本的 md_to_html.py 已删除）
- ❌ 决定调性 / 选题 → 参考 `wechat/公众号内容定位.md` 由 Viral_Writer_Skill 处理

## 常见坑

1. **IP 白名单**（错误 40164）：公众号后台 → 设置与开发 → 基本配置 → IP 白名单，加上 `curl ifconfig.me` 看到的出口 IP。
2. **access_token 缓存**：脚本会写 `~/.cache/wechat-publisher/token.json` 自动复用（7200s 有效期）。频繁失败时删掉缓存重试。
3. **thumb_media_id ≠ URL**（错误 40007）：封面必须先调素材上传接口拿到 media_id，不能直接传图片 URL。脚本已处理。
4. **正文图片要本地**：HTML 里如果有非微信域名的图片 URL，发布时会被微信替换或拦截。建议都用本地路径，让脚本上传后嵌入 CDN URL。
5. **HTML 必须自包含**：外部 CSS、外部 JS、外部字体都会被剥；web-artifacts-builder 产出时务必告诉它"内联一切"。
6. **草稿不是发布**：本 skill 只创建草稿。发布要用户在后台手动点。
