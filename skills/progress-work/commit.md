# commit · 提交

用户输入的 git 子命令里有 `commit`。先按 SKILL.md「git 子命令的公共规则」完成检查和确认，再对每个项目执行下面的步骤。

## 步骤

1. **能否提交**：检查结果是阻塞或无改动 → 跳过，写明原因。
2. **身份**：提交身份是 `Aaron <aaron4lawliet@gmail.com>`，或者用户已经确认用当前身份继续，才往下走。
3. **文案**：
   - 本次命令里已经 draft 过 → 用那份（用户改过就用改过的）；
   - 没有 → 按 draft.md 的方法写出来，**并把文案展示出来**。
4. **暂存**：暂存区为空时执行 `git add -A`；暂存后再看一眼 `git diff --cached --name-only`，确认里面没有检查阶段标出、用户没同意的风险文件。有的话停下来问。
5. **提交**：把文案写进 scratchpad 目录里的临时文件，用 `git commit -F <文件>` 提交（保证多行正文不走样），提交后删掉临时文件。
   - 不加 `--no-verify`，不 `--amend`；
   - hook 或提交失败 → 如实汇报错误，这个项目后面的命令（比如 push）不再执行。
6. **汇报**：提交哈希和标题。

## 示例

| 输入 | 处理 |
|---|---|
| `/progress-work commit` | 每个有改动的项目：写文案并展示 → 提交 |
| `/progress-work draft commit` | 每个项目：写文案 → 用这份文案提交 |
| `/progress-work commit push ai-skills` | 只对 ai-skills：写文案并展示 → 提交 → 推送 |
