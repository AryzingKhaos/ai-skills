#!/usr/bin/env python3
"""为 /progress-work record 收集各项目在一段日期内的改动证据。

证据来源：
1. Claude Code / Codex 会话日志：AI 改了哪些文件、当天相关的提问
2. 文件修改时间（mtime）：兜住 Bash / 手工 / 编辑器里的改动
3. git：提交（区分作者时间和提交时间）、未提交改动、被 git 忽略的改动

只输出证据，不做总结；总结由 Claude 读完证据后完成。
"""
import argparse
import datetime as dt
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROGRESS_FILE = "/Users/aaron/workspace/个人/工作相关/所有事项进度.md"
HOME = Path.home()
SKIP_NAMES = {".git", ".DS_Store", "node_modules", "__pycache__"}
CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
WEEKDAY = "一二三四五六日"
MAX_FILES = 8
MAX_PROMPTS = 8


class Project:
    def __init__(self, name, root, exclude):
        self.name = name
        self.root = root
        self.exclude = exclude

    def rel(self, path):
        """path 属于本项目且未被排除时返回相对路径（根目录本身返回 "."），否则返回 None。"""
        if not path:
            return None
        if path.startswith("file://"):
            path = path[len("file://"):]
        if path == self.root:
            return "."
        if not path.startswith(self.root + "/"):
            return None
        rel = path[len(self.root) + 1:]
        if any(part in SKIP_NAMES for part in rel.split("/")):
            return None
        for pattern in self.exclude:
            if pattern.endswith("/") and (rel + "/").startswith(pattern):
                return None
            if fnmatch.fnmatch(rel, pattern):
                return None
        return rel


def new_day():
    return {"ai": set(), "prompts": [], "cwd_prompts": [], "mtime": [], "commits": []}


def day_label(day):
    return f"{day.isoformat()} 周{WEEKDAY[day.weekday()]}"


def epoch(day):
    return dt.datetime.combine(day, dt.time()).timestamp()


def parse_ts(ts):
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return None


def run(cmd, stdin=None, ok_codes=(0,)):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, input=stdin, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode in ok_codes else None


def load_projects(path):
    """项目和目录来自「## 统计的项目地址」，记录排除来自「## 统计方式」里同名项目的小节。"""
    text = Path(path).read_text(encoding="utf-8")
    addresses = {}
    section = re.search(r"^## 统计的项目地址[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    for line in (section.group(1) if section else "").splitlines():
        match = re.match(r"^\s*(?:-\s*)?([^:：`\s]+)\s*[:：]\s*`?([^`]+?)`?\s*$", line)
        if match:
            addresses[match.group(1)] = match.group(2).rstrip("/")
    if not addresses:
        sys.exit(f"{path} 的「## 统计的项目地址」里没有 `项目名: 路径` 格式的行")

    excludes = {}
    section = re.search(r"^## 统计方式[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    for block in re.split(r"^### ", section.group(1) if section else "", flags=re.M)[1:]:
        name, _, body = block.partition("\n")
        exclude = re.search(r"^- 记录排除：(.*)$", body, re.M)
        excludes[name.strip().lower()] = re.findall(r"`([^`]+)`", exclude.group(1)) if exclude else []

    return [Project(name, root, excludes.get(name.lower(), [])) for name, root in addresses.items()]


def select_projects(projects, names):
    """按项目名（不区分大小写）筛选；names 为空时返回全部，有认不出的名字直接退出。"""
    if not names:
        return projects
    wanted = {name.lower() for name in names}
    unknown = wanted - {p.name.lower() for p in projects}
    if unknown:
        sys.exit(f"「统计的项目地址」里没有这些项目：{'、'.join(sorted(unknown))}；现有：{'、'.join(p.name for p in projects)}")
    return [p for p in projects if p.name.lower() in wanted]


def clean_prompt(text):
    command = re.search(r"<command-name>([^<]*)</command-name>", text)
    if command:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        text = f"{command.group(1).strip()} {args.group(1).strip() if args else ''}"
    elif text.lstrip().startswith(("<", "# AGENTS.md")):
        return ""
    text = " ".join(text.split())
    return text[:120] + ("…" if len(text) > 120 else "")


def recent_logs(pattern_roots, start):
    since = epoch(start)
    for root in pattern_roots:
        for path in root.glob("**/*.jsonl"):
            try:
                if path.stat().st_mtime >= since:
                    yield path
            except OSError:
                continue


def mark_cwd(projects, cwd, session, day, touched):
    for project in projects:
        if project.rel(cwd) is not None:
            touched[(session, day)].setdefault(project.name, False)


def scan_claude(projects, start, end, evidence, prompts, touched):
    bases = [HOME / ".claude"] + sorted(Path(p) for p in glob.glob(str(HOME / ".claude.*")) if os.path.isdir(p))
    seen = set()
    for log in recent_logs([base / "projects" for base in bases], start):
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line and '"type":"user"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                # 备份目录里有同一事件的副本，按 uuid 去重
                uid = event.get("uuid")
                if uid in seen:
                    continue
                seen.add(uid)
                when = parse_ts(event.get("timestamp", ""))
                if not when or not start <= when.date() <= end:
                    continue
                day, session = when.date(), event.get("sessionId")
                content = (event.get("message") or {}).get("content")
                if event.get("type") == "assistant" and isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        if block.get("name") not in CLAUDE_EDIT_TOOLS:
                            continue
                        args = block.get("input") or {}
                        path = args.get("file_path") or args.get("notebook_path") or ""
                        for project in projects:
                            rel = project.rel(path)
                            if rel:
                                evidence[project.name][day]["ai"].add(("claude", rel))
                                touched[(session, day)][project.name] = True
                elif event.get("type") == "user" and not event.get("isMeta") and isinstance(content, str):
                    mark_cwd(projects, event.get("cwd") or "", session, day, touched)
                    text = clean_prompt(content)
                    if text:
                        prompts[(session, day)].append(("claude", text))


def scan_codex(projects, start, end, evidence, prompts, touched):
    seen = set()
    for log in recent_logs([HOME / ".codex" / "sessions"], start):
        session = log.stem
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "item_completed" not in line and "turn_context" not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                when = parse_ts(event.get("timestamp", ""))
                if not when or not start <= when.date() <= end:
                    continue
                day, payload = when.date(), event.get("payload") or {}
                if event.get("type") == "turn_context":
                    mark_cwd(projects, payload.get("cwd") or "", session, day, touched)
                    continue
                item = payload.get("item") or {}
                item_id = item.get("id")
                if item_id:
                    if item_id in seen:
                        continue
                    seen.add(item_id)
                if item.get("type") == "FileChange":
                    for path in item.get("changes") or {}:
                        for project in projects:
                            rel = project.rel(path)
                            if rel:
                                evidence[project.name][day]["ai"].add(("codex", rel))
                                touched[(session, day)][project.name] = True
                elif item.get("type") == "UserMessage":
                    text = " ".join(
                        block.get("text", "") for block in item.get("content") or []
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                    text = clean_prompt(text)
                    if text:
                        prompts[(session, day)].append(("codex", text))


def scan_mtime(project, start, end, evidence):
    low, high = epoch(start), epoch(end + dt.timedelta(days=1))
    changed = []
    for dirpath, dirnames, filenames in os.walk(project.root):
        dirnames[:] = [d for d in dirnames if project.rel(os.path.join(dirpath, d)) is not None]
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = project.rel(full)
            if rel is None:
                continue
            try:
                mtime = os.lstat(full).st_mtime
            except OSError:
                continue
            if low <= mtime < high:
                stamp = dt.datetime.fromtimestamp(mtime)
                evidence[project.name][stamp.date()]["mtime"].append((rel, stamp))
                changed.append(full)
    return changed


def scan_git(project, start, end, evidence, changed):
    top = run(["git", "-C", project.root, "rev-parse", "--show-toplevel"])
    if top is None:
        return None
    top = top.strip()
    scope = os.path.relpath(project.root, top)
    git = ["git", "-C", top, "-c", "core.quotepath=false"]

    # 不设截止时间：延迟到范围之后才提交的改动也要能看到
    log = run(git + [
        "log", "--all", f"--since={start.isoformat()} 00:00:00", "--date=format:%Y-%m-%d %H:%M",
        "--pretty=format:@@%h\t%ad\t%cd\t%s", "--name-only", "--", scope,
    ]) or ""
    late = []
    for chunk in log.split("@@")[1:]:
        head, *files = chunk.strip("\n").split("\n")
        short, authored, committed, subject = head.split("\t", 3)
        files = [f for f in files if f and project.rel(os.path.join(top, f))]
        commit = {"hash": short, "authored": authored, "committed": committed, "subject": subject, "files": files}
        committed_day = dt.date.fromisoformat(committed[:10])
        if committed_day > end:
            late.append(commit)
        else:
            evidence[project.name][committed_day]["commits"].append(commit)

    dirty = []
    for line in (run(git + ["status", "--porcelain", "--untracked-files=all", "--", scope]) or "").splitlines():
        code, path = line[:2].strip(), line[3:].split(" -> ")[-1].strip('"')
        full = os.path.join(top, path)
        if project.rel(full) is None:
            continue
        try:
            stamp = dt.datetime.fromtimestamp(os.lstat(full).st_mtime).strftime("%m-%d %H:%M")
        except OSError:
            stamp = "已删除"
        dirty.append(f"{code} {path}（{stamp}）")

    ignored = run(git + ["check-ignore", "--stdin"], stdin="\n".join(changed), ok_codes=(0, 1)) if changed else ""
    ignored_count = len((ignored or "").splitlines())
    return {"top": top, "late": late, "dirty": dirty, "ignored": ignored_count}


def fmt_files(rels):
    rels = sorted(set(rels))
    if len(rels) <= MAX_FILES:
        return f"共 {len(rels)} 个：" + "、".join(rels)
    groups = Counter("/".join(os.path.dirname(r).split("/")[:3]) + "/" if "/" in r else r for r in rels)
    shown = "、".join(f"{g}（{n}）" for g, n in groups.most_common(MAX_FILES))
    more = f" 等 {len(groups)} 组" if len(groups) > MAX_FILES else ""
    return f"共 {len(rels)} 个，按目录：{shown}{more}"


def fmt_commit(commit):
    when = f"作者 {commit['authored'][5:]}"
    if commit["committed"] != commit["authored"]:
        when += f" / 提交 {commit['committed'][5:]}"
    return f"{commit['hash']} {when}「{commit['subject']}」（{len(commit['files'])} 个文件）"


def batch_hint(mtimes):
    """同一秒内大量文件同时变化，多半是 cp / git checkout / 同步盘，而不是真实编辑。"""
    per_second = Counter(stamp.replace(microsecond=0) for _, stamp in mtimes)
    if not per_second:
        return ""
    second, count = per_second.most_common(1)[0]
    return f"（{count} 个文件的修改时间都是 {second:%H:%M:%S}，疑似批量复制或同步）" if count >= 20 else ""


def main():
    today = dt.date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=today - dt.timedelta(days=today.weekday()))
    parser.add_argument("--end", type=dt.date.fromisoformat, default=today)
    parser.add_argument("--project", action="append", default=[], help="只看这些项目，可重复")
    parser.add_argument("--progress-file", default=PROGRESS_FILE)
    args = parser.parse_args()
    if args.start > args.end:
        sys.exit("--start 不能晚于 --end")

    projects = select_projects(load_projects(args.progress_file), args.project)
    usable = [p for p in projects if p.root and os.path.isdir(p.root)]

    evidence = defaultdict(lambda: defaultdict(new_day))
    prompts, touched = defaultdict(list), defaultdict(dict)
    scan_claude(usable, args.start, args.end, evidence, prompts, touched)
    scan_codex(usable, args.start, args.end, evidence, prompts, touched)
    # 会话里改过项目文件的提问和只是在项目目录下开的会话分开放：后者常是讨论或查询，不一定有改动
    for (session, day), names in touched.items():
        for name, edited in names.items():
            key = "prompts" if edited else "cwd_prompts"
            evidence[name][day][key].extend(prompts.get((session, day), []))

    print(f"# 改动证据 {day_label(args.start)} ~ {day_label(args.end)}")
    print("> 证据只说明哪天哪些文件变了、当时在问什么；写进表格前要读懂内容再概括。\n")
    for project in projects:
        print(f"## {project.name}（{project.root}）")
        if project not in usable:
            print("- 跳过：项目地址不存在\n")
            continue
        changed = scan_mtime(project, args.start, args.end, evidence)
        git = scan_git(project, args.start, args.end, evidence, changed)
        days = evidence[project.name]
        if not days:
            print("- 本期没有任何改动证据")
        for day in sorted(days):
            item = days[day]
            print(f"### {day_label(day)}")
            for source in ("claude", "codex"):
                files = [rel for src, rel in item["ai"] if src == source]
                if files:
                    print(f"- AI 修改[{source}]：{fmt_files(files)}")
            for key, label in (("prompts", "改动会话的提问"), ("cwd_prompts", "同目录会话的提问（会话里没有 Edit/Write 记录）")):
                unique = list(dict.fromkeys(item[key]))
                if unique:
                    shown = "；".join(f"[{src}] {text}" for src, text in unique[:MAX_PROMPTS])
                    more = f"（另有 {len(unique) - MAX_PROMPTS} 条）" if len(unique) > MAX_PROMPTS else ""
                    print(f"- {label}：{shown}{more}")
            if item["mtime"]:
                print(f"- 文件修改时间：{fmt_files(rel for rel, _ in item['mtime'])}{batch_hint(item['mtime'])}")
            for commit in item["commits"]:
                print(f"- git 提交：{fmt_commit(commit)}")
        print("### git 概况")
        if git is None:
            print("- 不是 git 仓库，没有 git 佐证")
        else:
            print(f"- 仓库：{git['top']}")
            for commit in git["late"]:
                print(f"- 范围结束后才提交：{fmt_commit(commit)}")
            print(f"- 未提交改动：{'、'.join(git['dirty']) if git['dirty'] else '无'}")
            if git["ignored"]:
                print(f"- 本期修改但被 git 忽略：{git['ignored']} 个文件（这些只能靠会话日志和修改时间佐证）")
        print()


if __name__ == "__main__":
    main()
