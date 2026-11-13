#!/usr/bin/env python3
"""核对 skills-usage.html 与 skills/*/SKILL.md 是否一致。

检查三件事：
  1. 每个 skill 都在网页里，网页里也没有多出来的；
  2. 网页的 hint 与 SKILL.md 的 argument-hint 完全相同；
  3. 每个 SKILL.md 都写了 argument-hint。

用法：python3 check_skills_usage.py    （不一致时退出码为 1）
"""
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
PAGE = ROOT / "skills-usage.html"


def read_frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    return yaml.safe_load(m.group(1)) if m else {}


def read_page_entries() -> dict:
    """从网页的 SKILLS 数组里取出每项的 name / dir / hint。"""
    src = PAGE.read_text(encoding="utf-8")
    block = src[src.index("const SKILLS = [") : src.index("\n];", src.index("const SKILLS = ["))]
    entries = {}
    for chunk in re.split(r"\n  \{\n", block)[1:]:
        fields = dict(re.findall(r'^\s{4}(name|dir|hint): "(.*)",$', chunk, re.M))
        entries[fields["name"]] = fields
    return entries


def main() -> int:
    page = read_page_entries()
    problems = []

    seen = set()
    for skill_md in sorted(ROOT.glob("skills/*/SKILL.md")):
        meta = read_frontmatter(skill_md)
        name = meta.get("name") or skill_md.parent.name
        hint = meta.get("argument-hint")
        seen.add(name)
        if hint is None:
            problems.append(f"{name}: SKILL.md 没有 argument-hint")
        entry = page.get(name)
        if entry is None:
            problems.append(f"{name}: 网页里没有这个 skill")
            continue
        if entry.get("dir", name) != skill_md.parent.name:
            problems.append(f"{name}: 网页 dir 应为 {skill_md.parent.name!r}")
        if hint is not None and entry.get("hint") != hint:
            problems.append(f"{name}: hint 不一致\n    SKILL.md: {hint}\n    网页:     {entry.get('hint')}")

    for name in sorted(set(page) - seen):
        problems.append(f"{name}: 网页里有，但 skills/ 下找不到")

    if problems:
        print("\n".join(problems))
        return 1
    print(f"OK：{len(seen)} 个 skill，网页与 SKILL.md 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
