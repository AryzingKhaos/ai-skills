#!/usr/bin/env python3
"""/progress-work draft / commit / push 之前检查各项目仓库的状态。

只读：不执行 add / commit / push，也不改 git 配置。默认不联网；加 --fetch 会先 git fetch，落后提交数才准。
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_changes import PROGRESS_FILE, load_projects, run, select_projects  # noqa: E402

EXPECTED_NAME = "Aaron"
EXPECTED_EMAIL = "aaron4lawliet@gmail.com"
RISKY_MARKS = (".env", "id_rsa", "id_ed25519", ".pem", ".key", ".p12", "credential", "secret")
LARGE_BYTES = 50 * 1024 * 1024


def git(top, *args):
    out = run(["git", "-C", top, "-c", "core.quotepath=false", *args])
    return None if out is None else out.rstrip("\n")


def lines(text):
    return [line for line in (text or "").splitlines() if line]


def identity_ok(name, email):
    return name == EXPECTED_NAME and email == EXPECTED_EMAIL


def risky_files(top, paths):
    found = []
    for rel in paths:
        if any(mark in os.path.basename(rel).lower() for mark in RISKY_MARKS):
            found.append(f"{rel}（文件名像密钥 / 凭证）")
            continue
        try:
            size = os.path.getsize(os.path.join(top, rel))
        except OSError:
            continue
        if size > LARGE_BYTES:
            found.append(f"{rel}（{size // 1024 // 1024}MB）")
    return found


def check(project, fetch):
    print(f"## {project.name}（{project.root}）")
    if not os.path.isdir(project.root):
        print("- ⛔ 阻塞：项目地址不存在\n")
        return
    top = git(project.root, "rev-parse", "--show-toplevel")
    if top is None:
        print("- ⛔ 阻塞：不是 git 仓库\n")
        return

    blockers, confirms = [], []
    if os.path.realpath(top) != os.path.realpath(project.root):
        confirms.append(f"项目地址不是仓库根目录，git 操作会作用于整个仓库 {top}")

    branch = git(top, "branch", "--show-current")
    if not branch:
        blockers.append("HEAD 处于分离状态（detached）")
    git_dir = git(top, "rev-parse", "--absolute-git-dir") or ""
    for marker, label in (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
                          ("CHERRY_PICK_HEAD", "cherry-pick")):
        if os.path.exists(os.path.join(git_dir, marker)):
            blockers.append(f"有未完成的 {label}")

    upstream = git(top, "rev-parse", "--abbrev-ref", "@{u}")
    fetch_note = ""
    if upstream and fetch:
        fetch_note = "" if git(top, "fetch", "--quiet", upstream.split("/")[0]) is not None else "（fetch 失败，数字可能不准）"
    elif upstream:
        fetch_note = "（未 fetch，落后数可能不准）"
    ahead = behind = 0
    if upstream:
        counts = git(top, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        if counts:
            behind, ahead = (int(n) for n in counts.split())
    remote_url = git(top, "remote", "get-url", "--push", upstream.split("/")[0]) if upstream else None

    staged = lines(git(top, "diff", "--cached", "--name-only"))
    unstaged = lines(git(top, "diff", "--name-only"))
    untracked = lines(git(top, "ls-files", "--others", "--exclude-standard"))
    to_commit = staged or sorted(set(unstaged) | set(untracked))

    name = git(top, "config", "user.name") or ""
    email = git(top, "config", "user.email") or ""
    origin = (git(top, "config", "--show-origin", "user.email") or "未设置").split("\t")[0]
    if to_commit and not identity_ok(name, email):
        confirms.append(f"提交身份是 {name or '（空）'} <{email or '（空）'}>，不是 {EXPECTED_NAME} <{EXPECTED_EMAIL}>（设置于 {origin}）")

    unpushed = [u.split("\t") for u in lines(git(top, "log", "@{u}..HEAD", "--pretty=format:%h\t%an\t%ae\t%cn\t%ce\t%s"))] if upstream else []
    wrong_authors = [u for u in unpushed if not (identity_ok(u[1], u[2]) and identity_ok(u[3], u[4]))]
    for short, an, ae, cn, ce, subject in wrong_authors:
        confirms.append(f"未推送提交 {short}「{subject}」作者 {an} <{ae}>、提交者 {cn} <{ce}>，不是期望身份")

    risky = risky_files(top, to_commit)
    if risky:
        confirms.append("将要提交的文件里有疑似敏感或过大的文件：" + "、".join(risky))
    if behind:
        confirms.append(f"落后上游 {behind} 个提交，push 可能被拒；不会自动 pull / rebase / force")

    print(f"- 仓库：{top} ｜ 分支：{branch or '（detached）'} ｜ 上游：{upstream or '无'}")
    if remote_url:
        print(f"- 推送地址：{remote_url}")
    if upstream:
        print(f"- 与上游：领先 {ahead}、落后 {behind}{fetch_note}")
    print(f"- 改动：暂存 {len(staged)}、未暂存 {len(unstaged)}、未跟踪 {len(untracked)}")
    if staged:
        print("- 提交范围：暂存区非空 → 只提交暂存区（未暂存和未跟踪的不提交）")
    elif to_commit:
        print("- 提交范围：暂存区为空 → commit 时 git add -A，提交全部改动")
    status = "✅" if identity_ok(name, email) else "⚠️"
    print(f"- 提交身份：{name or '（空）'} <{email or '（空）'}> {status}（设置于 {origin}）")
    print(f"- 未推送提交：{len(unpushed)} 个" + ("：" + "、".join(f"{u[0]} {u[5]}" for u in unpushed) if unpushed else ""))

    draft_state = "有改动" if to_commit else "无改动，跳过"
    commit_state = "⛔ 阻塞" if blockers else ("可执行" if to_commit else "无改动，跳过")
    if not upstream:
        push_state = "⛔ 没有上游分支，需要用户指定推到哪里"
    elif blockers:
        push_state = "⛔ 阻塞"
    else:
        push_state = "可执行" if ahead or to_commit else "没有要推送的提交，跳过"
    print(f"- draft：{draft_state} ｜ commit：{commit_state} ｜ push：{push_state}")
    for item in blockers:
        print(f"- ⛔ 阻塞：{item}")
    for item in confirms:
        print(f"- ⚠️ 需确认：{item}")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="append", default=[], help="只检查这些项目，可重复")
    parser.add_argument("--fetch", action="store_true", help="先 git fetch 再计算领先 / 落后")
    parser.add_argument("--progress-file", default=PROGRESS_FILE)
    args = parser.parse_args()

    projects = select_projects(load_projects(args.progress_file), args.project)

    print(f"# 仓库检查（只读） ｜ 期望提交身份：{EXPECTED_NAME} <{EXPECTED_EMAIL}>\n")
    for project in projects:
        check(project, args.fetch)


if __name__ == "__main__":
    main()
