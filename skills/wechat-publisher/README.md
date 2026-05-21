# wechat-publisher

把一份已经排好版的 HTML + 封面图，上传到微信公众号草稿箱。

**只做这一件事。** 写稿和排版都不在这个 skill 的职责里。

## 完整发稿流水线

由三个 skill 串成：

```
Viral_Writer_Skill          (写稿)
   ↓ dist/<topic>-公众号.md
web-artifacts-builder       (排版)
   ↓ dist/<topic>.html      ← 内联样式 / 自包含 / 无外部依赖
wechat-publisher            (上传 ← 这里)
   ↓
公众号后台草稿箱
```

## 安装

需要 Python 3.10+，依赖：

```bash
pip3 install --user requests
```

（之前依赖的 `markdown` 已不需要——md → HTML 转换由 web-artifacts-builder 接管）

## 配置

1. 复制 `config.example.json` 到一个安全位置（**不要入 git**）：

   ```bash
   cp config.example.json /Users/aaron/code/writerForSelfMedia/.wechat-config.json
   ```

2. 填入真实凭证：
   - `appid` / `appsecret`：公众号后台 → 设置与开发 → 基本配置
   - `author`：默认署名，可空

3. **把当前出口 IP 加入白名单**：公众号后台 → 设置与开发 → 基本配置 → IP 白名单。
   - 查当前出口 IP：`curl ifconfig.me`
   - 不加白名单调 API 会报 `40164`。

## 文件结构

```
wechat-publisher/
├── SKILL.md            # 给 Claude 看的编排流程
├── README.md           # 这份文件
├── config.example.json # 凭证模板
└── scripts/
    ├── publish.py      # CLI 入口（接 --html）
    ├── wechat_client.py# 微信 API 封装
    └── html_prep.py    # HTML 清洗（提取标题、上传图片、剥不兼容元素）
```

## 直接调用脚本（不经 Claude）

```bash
python3 scripts/publish.py \
  --config /path/to/.wechat-config.json \
  --html /path/to/article.html \
  --cover /path/to/cover.jpg \
  --digest "一句话摘要，54 字以内" \
  --author "Aaron"
```

参数说明在 `python3 scripts/publish.py --help`。

### 先 dry-run 看清洗结果

不调微信，只把 HTML 清洗后打印出来：

```bash
python3 scripts/publish.py --config any.json --html article.html --dry-run
```

`--config` 这一步会被忽略，可以传任意路径。

## 脚本帮你做的事

`html_prep.py` 在上传前会自动：

1. **提取标题**：优先 `<title>`，回退第一个 `<h1>`
2. **剥不兼容元素**：`<script>` / `<iframe>` / 外部 `<link rel="stylesheet">` 会被删；`<style>` 块会被警告（微信会剥掉，样式可能丢失）
3. **处理本地图片**：`<img src="cover.jpg">` 这类本地路径会被上传到微信，src 替换成 `mmbiz.qpic.cn/...`
4. **警告外链图**：非微信域名的 http(s) 图片会保留但 warn，建议改成本地

## 对 web-artifacts-builder 输出的要求

让 web-artifacts-builder 输出 HTML 时，**必须**：

- 单文件、自包含
- **所有样式 inline**（不要 `<style>` 块，不要 `<link rel="stylesheet">`）
- 不要 `<script>` / 不要 React / 不要任何运行时
- 字体不依赖外部 CDN
- 图片用相对路径指向本地文件

如果默认 artifact 是带 Tailwind / React 的，明确告诉它"输出 WeChat-compatible 静态 HTML，所有样式 inline"。

## 常见报错速查

| errcode | 原因 | 处理 |
|---|---|---|
| 40001 | access_token 失效 | `rm ~/.cache/wechat-publisher/token-*.json` 重试 |
| 40007 | media_id 无效 | 封面必须是上传后的 media_id，不是 URL |
| 40164 | IP 不在白名单 | 后台加白名单 |
| 45009 | 接口调用频次超限 | 等一会儿；检查是否反复刷 token |
| 48001 | API 未授权 | 订阅号没有图文素材权限；需服务号或认证订阅号 |

## 关于图片

- **封面图（thumb）**：本地路径，脚本会上传成永久素材，吃永久素材额度（5000 张上限）
- **正文图**：本地路径，脚本走 `media/uploadimg` 接口，**不占永久素材额度**，返回的微信 CDN URL 直接嵌入 HTML
- **外链图**：草稿能存，但发布时微信可能拦截或替换。**避免使用**
- **data URI**：不支持，会警告

## 注意

- 草稿 ≠ 发布。本工具只创建草稿；发布要去公众号后台手动点
- 摘要超过 54 字会被截断
- HTML 上微信前的"排版"工作交给 web-artifacts-builder；本 skill 不做 markdown→HTML 转换，也不做样式美化
