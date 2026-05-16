---
name: wechat-publisher
description: >
  端到端的微信公众号自媒体发稿流水线。用户给一个主题，Claude 联网调研、按照用户既定的写作风格生成完整文章草稿（含标题、正文、备选标题、配图指导），用户审核确认后通过微信公众号草稿箱 API 上传到草稿箱，由用户在公众号后台审核并发布。
  触发场景：'写一篇公众号'、'帮我写一篇关于 X 的公众号'、'把 X 主题做成公众号草稿'、'上传到公众号草稿箱'、'/wechat-publisher X'。
  与 viral-writer 的区别：viral-writer 只负责把主题写成 md；wechat-publisher 在此之上加了"上传到公众号后台草稿箱"这一步。
---

# WeChat Publisher 公众号一键发稿

完整工作流：**主题 → 调研 → 写稿 → 用户审核 → 上传草稿箱**。

最终的"发布"动作由用户在微信公众号后台完成；本 skill 只负责生成草稿。

## 工作目录约定

- 用户写作风格规范：`/Users/aaron/code/writerForSelfMedia/writingHabits/`
  - `知乎写作风格总结.md` — 用户长期写作风格（短刀/比较法/三段递进等）
  - `公众号调性规范-资深从业者复盘.md` — 公众号场景下的调性细则
- 文章草稿输出目录：`/Users/aaron/code/writerForSelfMedia/dist/`
- skill 自带脚本：`/Users/aaron/code/ai-skills/skills/wechat-publisher/scripts/`
- 微信公众号凭证：`/Users/aaron/code/writerForSelfMedia/.wechat-config.json`（不入 git）

## 启动前必须确认（用户没说就问）

1. **主题/角度**：用户大致想写什么。
2. **平台调性**：默认 `公众号调性规范-资深从业者复盘.md`；如果用户说"按 X 调性"，找对应规范。
3. **是否上传**：默认写完后等用户审核再上传，但要让用户知道流程末尾会调 publish.py。

## 工作流

### 第一步：联网调研

用 WebSearch / WebFetch 收集 3-6 条权威信息源。**优先一手来源**（官方文章、当事人博客/Twitter、知名媒体），其次是聚合解读。

每条来源记下：标题、URL、核心要点。最后会作为 `## Sources` 附在文章末尾。

### 第二步：内化两份写作风格规范

每次都必须读：

1. `Read /Users/aaron/code/writerForSelfMedia/writingHabits/知乎写作风格总结.md`
2. `Read /Users/aaron/code/writerForSelfMedia/writingHabits/公众号调性规范-资深从业者复盘.md`

不要凭记忆写——用户的风格会随时间更新，这两份文件是事实源。

### 第三步：生成完整草稿

按 `公众号调性规范` 第五节"典型结构骨架"组织正文。

输出文件保存到 `/Users/aaron/code/writerForSelfMedia/dist/<主题简称>-公众号.md`，文件结构：

```markdown
# <最终标题>

> 平台：微信公众号 | 字数：约 X 字 | 调性：<对应规范名>

---

<正文 1500-3000 字>

---

## 备选标题
1. ... — 策略：...
（共 5 条）

## 配图指导
### 封面图
- 推荐比例：2.35:1
- 生成 prompt：...

### 正文配图
#### 配图 1（位置：第 X 节后）
...

---

## Sources
- [Title](URL)
...
```

写完后给用户简要汇报：标题、字数、核心论点 1 句话、是否要调整。**等用户确认后再进入第四步。**

### 第四步：准备发布版

用户确认后，从草稿 md 抽出"纯发布部分"，保存为 `<主题简称>-公众号.publish.md`。

发布版只包含：
- 第一行 H1 标题
- 正文（从开头摘要 blockquote 之后，到 `## 备选标题` 之前的所有内容）
- 末尾的 `## Sources`（可选，公众号读者一般不需要外链，**默认不带**）

发布版规则：
- 第一行必须是 `# <标题>`，脚本据此提取 title
- 正文里图片用本地相对路径或 `https://...` 绝对路径都行，脚本会把本地图片上传到微信素材库并替换 URL
- 摘要 blockquote（`> 平台：...`）不要带

### 第五步：准备封面图（可选但强烈建议）

公众号草稿没有封面图也能存，但发布时必须有。建议在这一步处理：

1. 把"配图指导"里的封面 prompt 给用户，让用户用 AI 画图工具（Midjourney / 即梦 / DALL-E 等）生成。
2. 用户把图片放到 `dist/cover-<主题简称>.jpg`（或 .png），告诉你路径。
3. 脚本会把它上传成"永久素材"，拿到 thumb_media_id 后塞进草稿。

如果用户没有封面图，跳过 `--cover` 参数即可，草稿不带封面（发布前用户在公众号后台补）。

### 第六步：调用脚本上传草稿

用 Bash 调用：

```bash
python3 /Users/aaron/code/ai-skills/skills/wechat-publisher/scripts/publish.py \
  --config /Users/aaron/code/writerForSelfMedia/.wechat-config.json \
  --md /Users/aaron/code/writerForSelfMedia/dist/<主题简称>-公众号.publish.md \
  --cover /Users/aaron/code/writerForSelfMedia/dist/cover-<主题简称>.jpg \
  --digest "<摘要 54 字内>" \
  --author "<作者名，可省略>"
```

参数说明：

- `--config`：微信凭证 JSON，含 `appid` / `appsecret`；首次使用照 `config.example.json` 抄一份。
- `--md`：发布版 md 路径。
- `--cover`：封面图本地路径；省略则草稿不带封面。
- `--digest`：摘要，54 字以内，公众号文章列表会显示。
- `--author`：作者署名，可省。

成功后脚本会返回草稿 `media_id`，并打印公众号后台草稿箱链接：
`https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_card&type=10&start=0&count=10`

让用户去后台看效果、补封面（如果没传）、最后点发布。

## 注意事项 / 常见坑

1. **IP 白名单**：微信 API 要求调用 IP 在 MP 后台白名单里。第一次跑提示 `40164` 错误就是这个原因——把当前出口 IP 加到"公众号设置 → 基本配置 → IP 白名单"。
2. **access_token 缓存**：token 有效期 7200s。脚本会写 `~/.cache/wechat-publisher/token.json` 自动复用，不要频繁调 token 接口（每天有配额）。
3. **thumb_media_id ≠ URL**：封面图必须是上传素材库后的 media_id，不是图片 URL。脚本已经处理好了；如果用户直接给 URL，不行。
4. **正文图片**：md 里的本地图片路径会被自动上传到微信图文素材库（永久），转换成微信 CDN URL 后嵌入 HTML。外链图片（非微信域名）在草稿里可能能看到，但发布时会被微信替换/拦截，**建议都用本地图**。
5. **HTML 风格化**：脚本只做基本 markdown → HTML 转换，不加复杂样式。如果用户想要更精美的排版，让他在后台用编辑器调整。
6. **草稿不是发布**：本 skill 只负责创建草稿。发布要用户在后台手动点。

## 与用户既有风格规范的关系

- 知乎短刀风格：用户原生风格，本 skill 不直接套用——公众号场景下需要把"扎"收着、把"评判他人"换成"我自己怎么用"。
- 公众号调性规范：本 skill 默认按这份执行（资深从业者复盘 + 思辨记录 + 同行分享）。

如果用户某次说"这篇用 X 调性"，就找对应规范文件读了再写；找不到就跟用户确认，并问要不要在 writingHabits 下新增一份规范。
