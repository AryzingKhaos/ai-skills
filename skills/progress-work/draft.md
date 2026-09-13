# draft · 写英文提交文案

用户输入的 git 子命令里有 `draft`。先按 SKILL.md「git 子命令的公共规则」完成检查和确认，再对每个项目执行下面的步骤。

**draft 只读**：不 `git add`、不 commit，不改变工作区和暂存区。

## 步骤

1. **没有改动** → 这个项目写"无改动，跳过"。
2. **按提交范围看改动**（范围规则见 SKILL.md）：
   - 暂存区非空：`git diff --cached --stat` + `git diff --cached`；
   - 暂存区为空：`git status --short` + `git diff --stat` + `git diff`，未跟踪的新文件读文件内容（大文件、生成文件只看文件名和开头）。
3. **看仓库已有的提交风格**：`git log -10 --oneline`。
4. **写英文提交文案，每个项目一条提交**，不拆分：
   - **标题**：`type: summary`
     - type 用 Conventional Commit 类型：`feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf` / `build` / `ci`；
     - summary 小写开头的祈使句，整行不超过 72 个字符，末尾不加句号；
     - 风格和仓库已有提交保持一致。
   - **正文**：改动涉及多个主题时，空一行后每个主题一行 `- ...`；单一主题可以不写正文。
   - **只写 diff 里真实存在的改动**，不编造目的和效果；类型判断不了的，在文案下面用中文说明不确定的地方。
5. **输出**：每个项目一个代码块，代码块上方标明项目名和提交范围（只含暂存区 / 全部改动）。

## 和 commit 连用

`draft commit` 连用时，commit 直接使用这里生成的文案，不再重写。用户看完 draft 后在同一轮对话里改了措辞，commit 以用户改过的为准。

## 示例

| 输入 | 处理 |
|---|---|
| `/progress-work draft` | 为「统计的项目地址」里每个有改动的项目写一条文案 |
| `/progress-work draft llmWikis` | 只为 llmWikis 写 |

文案示例：

```text
feat: add progress-work git subcommands

- add draft, commit and push with a shared repository preflight check
- read project list from the progress file instead of a fixed set
```
