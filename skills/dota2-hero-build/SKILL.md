---
name: dota2-hero-build
description: "从 B 站教学视频提炼某个 Dota2 英雄在【当前版本】该怎么出装。四步：①用 Valve 官方 datafeed 精确解析英雄与版本链 ②搜 B 站后过纯元数据闸门（其它游戏、DotA1 标记、UP主黑名单、易混英雄、无 Dota 语境、时长下限）③对少量候选只下 8 秒片段抽帧，看图核验是不是 Dota2 现代客户端 ④只对核验通过的视频全量下载+whisper 转写，抽出【条件 → 出装 → 理由 → 反例】四元组，保留分歧不求共识。**核心是省：绝不对无关视频做下载和转写**——元数据闸门零成本，画面核验约 1/600 成本，只有过了两关才花大钱。当前小版本凑不满 3 个相关视频就沿小版本链往前找（7.41e→7.41d→…→7.41），**绝不跨大版本**。跑完自动删掉 mp4/wav 等中间产物，只留报告、转写稿和少量关键帧。产出存 /Users/aaron/workspace/aiDownload/dota2/<英雄>-<版本>-<日期>/。**只有显式调用才能使用此 skill**，禁止模糊匹配触发——用户随口问『虚空该出什么装』不算。显式调用形式：'/dota2-hero-build 虚空假面'、'/dota2-hero-build 敌法师 --patch 7.41d'、'用 dota2-hero-build 分析一下宙斯本版本出装'。"
---

# dota2-hero-build · 从 B 站教学视频提炼版本出装

姊妹 skill：`dota2-match-review`（单局复盘）、`dota2-playtime`（时长预算）。

## 这个 skill 的设计前提（来自 2026-09-04 实测，别推翻它）

对虚空假面 @ 7.41e 做过一次完整 spike，结论写死在这里：

1. **语料极稀**。7.41e 窗口内全 B 站 112 个候选，真正可用的教学视频 **1 个**。
   所以目标是「找到 1–3 个**相关**的」，不是「找很多」。**宁可少，不可脏。**
2. **播放量与可信度负相关**。当时播放第一（107,969）的是《皇室战争》，
   唯一可用的教学视频只有 959 播放。**不要按播放量选片。**
3. **三种污染，防御成本差两个数量级**：
   | 污染 | 例 | 靠什么拦 | 成本 |
   |---|---|---|---|
   | 别的游戏 | 皇室战争、星露谷、群星 | 标签/标题关键词 | **零** |
   | 别名太泛 | 「虚空」匹配到公开课 | Dota 语境正向闸门 | **零** |
   | **DotA1 冒充** | DotA 6.83 讲虚空出装 | **只有画面** | 低（8 秒片段） |
   ⚠️ DotA1 那类的标题、标签、简介、**连转写稿都完全干净**——转写里同样是
   BKB/疯脸/跳刀。纯文本管线会安静地把 6.83 的出装写进 7.41e 的分析里。
4. **转写够用**：装备名 100% 出错（`A杖`→"A战/黄战"、`疯脸`→"封脸"），但 100% 可用
   别名表恢复；**条件性论述 100% 完整可读**。所以不上 OCR。
5. **whisper 静音段会幻觉**（"优优独播剧场——YoYo Television Series Exclusive"），
   转写字数过少的视频要直接判为无解说、弃用。

## 权威文件

| 用途 | 路径 |
|---|---|
| 版本链 / 英雄 / 装备官方中文名 | `scripts/dota_meta.py`（Valve datafeed，本地缓存 24h） |
| **黑话、黑白名单、污染源**（长期资产） | `scripts/slang.json` |
| 搜索 + 元数据闸门 | `scripts/find_videos.py` |
| 画面核验（8 秒片段抽帧） | `scripts/probe_frames.py` |
| 全量下载 + 转写 | `scripts/fetch_and_transcribe.py` |
| 收尾清理 | `scripts/cleanup.py` |
| 产出目录 | `/Users/aaron/workspace/aiDownload/dota2/<英雄>-<版本>-<日期>/` |

## 执行流程

### 0. 定英雄与版本

```bash
python3 scripts/dota_meta.py --hero 虚空假面      # 精确解析，拒绝模糊匹配
python3 scripts/dota_meta.py --patches           # 当前版本
python3 scripts/dota_meta.py --hero-changes 虚空假面 --patch 7.41e
```

**英雄名必须精确匹配官方中文名**，脚本不做模糊猜测；匹配不上就列出相近英雄让用户重选。
`--hero-changes` 拿到的是**版本事实基线**，后面用它核对 UP 主的说法对不对——
spike 里那个「虚空史诗级重做」的标题，对照官方数据是 7.41e **零改动**。

### 1. 闸门一：搜索 + 元数据筛选（零下载成本）

```bash
OUT="/Users/aaron/workspace/aiDownload/dota2/虚空假面-7.41e-$(date +%Y%m%d)"
mkdir -p "$OUT"
python3 scripts/find_videos.py --hero 虚空假面 --want 3 --out "$OUT/candidates.json"
```

脚本自动做：按小版本窗口过滤发布时间 → 六道元数据闸门 → 教学意图打分 →
**数量不足 `--want` 时沿小版本链往前找，到大版本边界为止（绝不跨大版本）**。

看输出里的「元数据闸门淘汰 N 个」——这 N 个就是省下来的下载和转写。

### 2. 闸门二：画面核验（约 1/600 成本）⭐ 不可省

```bash
python3 scripts/probe_frames.py --cand "$OUT/candidates.json" --outdir "$OUT/probe" --top 8
```

每个候选下 2 段 8 秒片段各抽 1 帧（~1.2MB/段，抽完立刻删片段）。

**然后你必须逐帧 Read 看图**，脚本做不了这个判断。每帧回答四件事：

| 看什么 | 通过 | 淘汰 |
|---|---|---|
| 界面 | Dota2 现代 HUD | **魔兽 III 界面 / 右上角 `DotA6.8x` / 英雄叫「暗惧者」→ DotA1** |
| 游戏 | 确实是 Dota2 | 别的游戏（元数据漏网的） |
| 英雄 | HUD 英雄面板是目标英雄 | 是别的英雄 |
| 形态 | 有解说/教学迹象 | 纯集锦、无人声、纯直播挂机 |

顺带：天赋树和 HUD 能**独立确认客户端版本**（例如 `-1s Time Walk Cooldown`
对应 7.41c 的改动），比发布日期可靠。

核验完把结论讲给用户：哪几个过了、哪几个因为什么被砍。

### 3. 只对通过的视频做全量处理

```bash
python3 scripts/fetch_and_transcribe.py --bvid BV1pYtH6MEje --hero 虚空假面 --outdir "$OUT"
```

下载 480p → 抽 wav → 抽关键帧 → whisper `large-v3-turbo` 转写（带术语偏置）→
**处理完立刻删 mp4/wav**。

转写字数 < 800 会告警：那是集锦/无解说，**不要拿它做出装分析**，回到第 2 步换候选。

### 4. 抽取与综合（你来做，脚本做不了）

读 `segments.txt`，用 `slang.json` 的 `item_slang` / `asr_fixes` 纠错，抽出四元组：

```
触发条件 → 出装/顺序调整 → 理由 → 反例（什么时候不要这么出）
```

**装备名只是锚点，理由才是要的东西。反例信息量最大，优先保。**

### 5. 出报告

写到 `$OUT/出装分析-<英雄>-<版本>.md`，**证据必须分级**：

- 🟢 **有视频依据** —— 哪个 UP 主、哪个 BV、第几分几秒说的
- 🟡 **只有版本依据** —— 从官方 patch notes 推的，**明确写「这不是任何 UP 主说的」**
- 🔴 **无法回答** —— 老实写出来

同时保留一份整理版文字稿 `文字稿-<BVID>-整理版.md`（纠错后 + 分段小标题 + ASR 错词清单）。

### 6. 收尾清理（每次都做）

```bash
python3 scripts/cleanup.py --dir "$OUT" --keep BV1pYtH6MEje --max-frames 8
```

删掉 mp4/wav/临时片段/被淘汰候选的整个目录与核验帧；留报告、转写稿、少量关键帧。
实测能从 ~570MB 压到几百 KB。先 `--dry-run` 看一眼也行。

### 7. ⭐ 回写 slang.json（这一步决定 skill 会不会越用越好）

这次跑下来只要发现新东西，**当场写回 `scripts/slang.json`**：

- 新的污染源 UP 主 → `uploader_blacklist`（附一句为什么）
- 核验通过、讲得好的 UP 主 → `uploader_whitelist`（**这是最有价值的资产**，
  一个好 UP 主能长期供片；一个坏 UP 主会批量污染候选池）
- 新的别名/易混英雄 → `hero_aliases` / `hero_conflicts`
  ⚠️ **宁缺勿滥**：一条错别名会把无关英雄拉进来，比漏掉更糟
  （反例：「小狗」是噬魂鬼，不是虚空假面）
- 新的 ASR 错词 → `item_slang` / `asr_fixes`
- 新的串味游戏 → `other_game_markers`

## ⛔ 硬规则

1. **绝不对没过两道闸门的视频做下载或转写。** 这是本 skill 存在的理由。
2. **绝不跨大版本回退。** 7.41e 可以退到 7.41，**不能退到 7.40**——跨大版本的出装结论无效。
3. **绝不按播放量选片。** 实测播放第一是《皇室战争》。
4. **绝不用统计数据冒充 UP 主的观点。** OpenDota 的出装胜率是结果反推原因
   （大炮胜率高是因为顺风才买得起，不是因为它强），只能做证伪层，且必须标成 🟡。
5. **绝不求共识。** 多个 UP 主给不同出装是常态，保留分歧和各自的适用条件——
   把它们平均掉就洗掉了最值钱的信息。
6. **英雄名不做模糊匹配。** 匹配不上就列相近英雄让用户重选。
7. **转写字数过少 = 无解说**，直接弃用，不要硬凑分析。

## 命令速查

```bash
python3 scripts/dota_meta.py --patches | --chain 7.41e | --hero 虚空假面
python3 scripts/dota_meta.py --hero-changes 虚空假面 --patch 7.41e
python3 scripts/find_videos.py --hero X --want 3 --out cand.json [--patch 7.41d] [--no-fallback]
python3 scripts/probe_frames.py --cand cand.json --outdir probe --top 8
python3 scripts/fetch_and_transcribe.py --bvid BVxxx --hero X --outdir OUT [--keep-media]
python3 scripts/cleanup.py --dir OUT --keep BVxxx [--max-frames 8] [--dry-run]
```

## 前置依赖

`yt-dlp`、`ffmpeg`、`openai-whisper`（python 包）、浏览器里登录过 bilibili.com。
B 站 412 反爬必须靠 `--cookies-from-browser`，脚本已内置。
联网命令在沙箱里会被拦，需要 `dangerouslyDisableSandbox: true`。
