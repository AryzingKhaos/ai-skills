# wechat-publisher

把一个主题，端到端做成微信公众号草稿。

## 流程

1. 用户给主题 / 角度
2. Claude 联网调研
3. Claude 读 `writingHabits/` 下的写作风格规范
4. Claude 生成完整 md 草稿（含正文、备选标题、配图指导、Sources）放到 `dist/`
5. 用户审核 → Claude 抽出"发布版" `.publish.md`
6. 用户提供封面图（可选）
7. `scripts/publish.py` 把 .publish.md + 封面上传到公众号草稿箱
8. 用户去公众号后台审核 → 发布

## 安装

需要 Python 3.10+，依赖：

```bash
pip install requests markdown
```

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
├── SKILL.md            # 给 Claude 看的工作流
├── README.md           # 这份文件
├── config.example.json # 凭证模板
└── scripts/
    ├── publish.py      # CLI 入口
    ├── wechat_client.py# 微信 API 封装
    └── md_to_html.py   # markdown → 微信 HTML
```

## 直接调用脚本（不经 Claude）

```bash
python3 scripts/publish.py \
  --config /path/to/.wechat-config.json \
  --md /path/to/article.publish.md \
  --cover /path/to/cover.jpg \
  --digest "一句话摘要，54 字以内" \
  --author "Aaron"
```

参数说明在 `python3 scripts/publish.py --help` 里。

### 先 dry-run 看 HTML

不会调微信，只把 md 转成 HTML 打印出来：

```bash
python3 scripts/publish.py --config any.json --md article.publish.md --dry-run
```

（`--config` 这一步会被忽略，可以传任意路径）

## 常见报错速查

| errcode | 原因 | 处理 |
|---|---|---|
| 40001 | access_token 失效 | `rm ~/.cache/wechat-publisher/token-*.json` 重试 |
| 40007 | media_id 无效 | 封面必须是上传后的 media_id，不是 URL |
| 40164 | IP 不在白名单 | 后台加白名单 |
| 45009 | 接口调用频次超限 | 等一会儿；检查是否在循环里反复刷 token |
| 48001 | API 未授权 | 订阅号没有图文素材权限；需服务号或认证订阅号 |

## 关于图片

- **封面图（thumb）**：本地路径，脚本会上传成永久素材，吃永久素材额度（5000 张上限）。建议复用同一张做多篇时手动复用 media_id。
- **正文图**：本地路径，脚本会通过 `media/uploadimg` 上传，**不占永久素材额度**，返回的微信 CDN URL 直接嵌入 HTML。
- **外链图**：草稿能存，但发布时微信可能拦截或替换。**避免使用**。

## 注意

- 草稿 ≠ 发布。本工具只创建草稿；发布要去公众号后台手动点。
- 摘要超过 54 字会被微信截断（中文按字符算）。
- HTML 样式是保守的内联样式。如果要更精美的排版，建议在公众号后台编辑器里调整。
