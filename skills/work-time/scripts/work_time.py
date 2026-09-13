#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计 Claude Code + Codex 的 AI 工作时长，并按完整项目路径分类。

核心规则：
- 发现 ~/.claude、全部 ~/.claude.* 目录和 ~/.codex/sessions 中的 JSONL；正常只读取变化文件的新增尾部。
- 将计时所需的最小原始字段按本地自然周缓存到工作时长-originData 目录，不保存对话正文。
- 从工作时长.md 的“项目目录”分组列表读取工作项目、planB 项目路径；其余归为其他项目。
- 先按完整项目路径把事件归入工作、planB 或其他，再在各分类内部独立切连续段。
- 同一分类内相邻事件间隔不超过 30 分钟才合并；每个分类段默认加 5 分钟准备和 5 分钟阅读。
- 自动维护三类时间段和按自然周统计；定位始终是观测，不是考核。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as datetime_module
import fnmatch
import glob
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS/Linux 均可用，仅为其它平台保留降级路径。
    fcntl = None


DEFAULT_LEDGER = Path("/Users/aaron/workspace/个人/生活/工作时长.md")
DEFAULT_ORIGIN = Path("/Users/aaron/workspace/个人/生活/工作时长-originData")
DEFAULT_CODEX_GLOB = os.path.expanduser("~/.codex/sessions/**/*.jsonl")

CATEGORIES = ("工作项目", "planB项目", "其他项目")
DETAIL_HEADINGS = {
    "工作项目": "工作项目时间段",
    "planB项目": "planB项目时间段",
    "其他项目": "其他项目时间段",
}
WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")
ORIGIN_MARKER = "<!-- work-time-origin-v1 -->"
WEEKLY_ORIGIN_MARKER = "<!-- work-time-weekly-origin-v1 -->"
SCAN_STATE_VERSION = 1
SCAN_STATE_NAME = ".scan-state-v1.json"
SCAN_LOCK_NAME = ".scan.lock"
CURSOR_HASH_BYTES = 4096
WEEKLY_MANUAL_NOTE = (
    "> planB项目包含通过 `start` 开启并由 `end`/`endTime` 完成的手动打卡；"
    "手动段按精确起止计时，不加准备/阅读补偿。"
)


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: datetime_module.datetime
    source: str
    project_path: str
    event_type: str


@dataclass(frozen=True)
class ClassifiedEvent:
    timestamp: datetime_module.datetime
    category: str
    project_path: str
    source: str


@dataclass(frozen=True)
class Segment:
    category: str
    start: datetime_module.datetime
    end: datetime_module.datetime
    seconds: int
    project_paths: tuple[str, ...]


@dataclass
class IncrementalScanResult:
    events: dict[str, Event]
    file_states: dict[str, dict[str, object]]
    source_counts: dict[str, int]
    stats: dict[str, int | str]


def parse_timestamp(value: object) -> datetime_module.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime_module.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime_module.timezone.utc)
        return parsed.astimezone()
    except (TypeError, ValueError):
        return None


def normalize_project_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "未知路径"
    value = value.strip()
    if "://" in value:
        return value
    return os.path.normpath(os.path.expanduser(value))


def fmt_duration(seconds: float | int) -> str:
    rounded = int(round(seconds))
    hours = rounded // 3600
    minutes = (rounded % 3600) // 60
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


def fmt_compact_duration(seconds: float | int) -> str:
    minutes = int(round(seconds)) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def fmt_date_weekday(value: datetime_module.datetime | datetime_module.date) -> str:
    return f"{value:%Y-%m-%d} 周{WEEKDAY_CN[value.weekday()]}"


def md_escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def md_unescape(value: str) -> str:
    return value.replace("\\|", "|").replace("\\\\", "\\")


def split_md_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    pieces = re.split(r"(?<!\\)\|", stripped)
    if pieces and pieces[0] == "":
        pieces = pieces[1:]
    if pieces and pieces[-1] == "":
        pieces = pieces[:-1]
    return [md_unescape(piece.strip()) for piece in pieces]


def md_row(values: list[object] | tuple[object, ...]) -> str:
    return "| " + " | ".join(md_escape(value) for value in values) + " |"


def atomic_write(path: Path, text_writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        text_writer(handle)
    os.replace(temporary, path)


def section_match(text: str, heading_prefix: str) -> re.Match[str] | None:
    return re.search(
        rf"(?ms)^## {re.escape(heading_prefix)}[^\n]*\n.*?(?=^## |\Z)",
        text,
    )


def section_text(text: str, heading_prefix: str) -> str:
    match = section_match(text, heading_prefix)
    return match.group(0) if match else ""


def replace_section(text: str, heading_prefix: str, replacement: str) -> str:
    match = section_match(text, heading_prefix)
    replacement = replacement.rstrip() + "\n\n"
    if match:
        return text[: match.start()] + replacement + text[match.end() :]
    insertion = re.search(r"(?m)^## 手动打卡明细", text)
    index = insertion.start() if insertion else len(text)
    return text[:index].rstrip() + "\n\n" + replacement + text[index:].lstrip("\n")


def remove_section(text: str, heading_prefix: str) -> str:
    match = section_match(text, heading_prefix)
    if not match:
        return text
    return text[: match.start()] + text[match.end() :]


def parse_project_config(ledger_text: str) -> dict[str, list[str]]:
    result = {"工作项目": [], "planB项目": []}
    block = section_text(ledger_text, "项目目录")
    if block:
        current = None
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("### 工作项目"):
                current = "工作项目"
            elif stripped.startswith("### planB"):
                current = "planB项目"
            elif stripped.startswith("### "):
                current = None
            elif current and stripped.startswith("-"):
                value = stripped[1:].strip().strip("`").strip()
                if value:
                    result[current].append(normalize_project_path(value))
    else:
        table = section_text(ledger_text, "项目分类")
        for line in table.splitlines():
            cells = split_md_row(line)
            if len(cells) < 2:
                continue
            category = cells[0].replace(" ", "")
            if category not in result:
                continue
            value = cells[1].strip().strip("`").strip()
            if value:
                result[category].append(normalize_project_path(value))
    for category in result:
        result[category] = list(dict.fromkeys(result[category]))
    if not result["工作项目"] and not result["planB项目"]:
        raise RuntimeError("工作时长.md 中没有找到可用的项目目录路径列表")
    return result


def render_project_config(config: dict[str, list[str]]) -> str:
    lines = [
        "## 项目目录",
        "### 工作项目",
    ]
    lines.extend(f"- `{pattern}`" for pattern in config["工作项目"])
    lines.extend(["", "### planB 项目"])
    lines.extend(f"- `{pattern}`" for pattern in config["planB项目"])
    return "\n".join(lines)


def normalize_config_section(ledger_text: str, config: dict[str, list[str]]) -> str:
    # “项目目录”是用户维护的权威配置；已有时保持其原始排版和附加说明不动。
    if section_match(ledger_text, "项目目录"):
        return ledger_text
    rendered = render_project_config(config)
    if section_match(ledger_text, "项目分类"):
        return replace_section(ledger_text, "项目分类", rendered)
    header_end = re.search(r"(?m)^## ", ledger_text)
    index = header_end.start() if header_end else len(ledger_text)
    return ledger_text[:index].rstrip() + "\n\n" + rendered + "\n\n" + ledger_text[index:]


def path_matches(project_path: str, pattern: str) -> bool:
    if project_path == "未知路径" or "://" in project_path:
        return False
    project_path = normalize_project_path(project_path)
    pattern = normalize_project_path(pattern)
    if not glob.has_magic(pattern):
        return project_path == pattern or project_path.startswith(pattern.rstrip("/") + "/")
    candidate = Path(project_path)
    paths = (str(candidate), *(str(parent) for parent in candidate.parents))
    return any(fnmatch.fnmatchcase(value, pattern) for value in paths)


def classify_project(project_path: str, config: dict[str, list[str]]) -> str:
    for category in ("工作项目", "planB项目"):
        if any(path_matches(project_path, pattern) for pattern in config[category]):
            return category
    return "其他项目"


def discover_claude_roots(explicit_roots: list[str] | None = None) -> list[Path]:
    if explicit_roots:
        candidates = [Path(os.path.expanduser(item)) for item in explicit_roots]
    else:
        candidates = [Path(os.path.expanduser("~/.claude"))]
        candidates.extend(Path(item) for item in sorted(glob.glob(os.path.expanduser("~/.claude.*"))))
    result = []
    seen = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        key = str(candidate.absolute())
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def configured_path_candidates(config: dict[str, list[str]]) -> list[str]:
    result = []
    for pattern in config["工作项目"] + config["planB项目"]:
        if glob.has_magic(pattern):
            result.extend(path for path in glob.glob(pattern) if os.path.isdir(path))
        else:
            result.append(pattern)
    return list(dict.fromkeys(normalize_project_path(path) for path in result))


def claude_fallback_path(path: Path, root: Path, configured_paths: list[str]) -> str:
    try:
        encoded = path.relative_to(root / "projects").parts[0]
    except (ValueError, IndexError):
        encoded = path.parent.name
    for candidate in configured_paths:
        encoded_candidate = "-" + candidate.strip("/").replace("/", "-")
        if encoded_candidate == encoded:
            return candidate
    return f"claude-project://{encoded}"


def codex_fallback_path(path: Path) -> str:
    return f"codex-session://{path.stem}"


def cwd_from_record(record: dict) -> str | None:
    direct = record.get("cwd")
    if isinstance(direct, str) and direct.strip():
        return normalize_project_path(direct)
    payload = record.get("payload")
    if isinstance(payload, dict):
        nested = payload.get("cwd")
        if isinstance(nested, str) and nested.strip():
            return normalize_project_path(nested)
    return None


def detect_file_cwd(path: Path, fallback: str) -> str:
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for index, line in enumerate(handle):
                if index >= 200:
                    break
                stripped = line.strip()
                if not stripped.startswith("{"):
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                cwd = cwd_from_record(record)
                if cwd:
                    return cwd
    except OSError:
        pass
    return fallback


def event_type_of(record: dict) -> str:
    value = record.get("type")
    if isinstance(value, str):
        return value
    payload = record.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("type"), str):
        return payload["type"]
    return "unknown"


def _digest_file_range(handle, start: int, end: int) -> str:
    """Hash a small, stable byte range without changing the caller's file position."""
    current = handle.tell()
    digest = hashlib.sha256()
    try:
        handle.seek(max(0, start))
        remaining = max(0, end - max(0, start))
        while remaining:
            chunk = handle.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    finally:
        handle.seek(current)
    return digest.hexdigest()


def _file_identity(stat_result: os.stat_result) -> tuple[int, int]:
    return int(stat_result.st_dev), int(stat_result.st_ino)


def _same_file_identity(cursor: dict[str, object], stat_result: os.stat_result) -> bool:
    try:
        return (
            int(cursor["device"]),
            int(cursor["inode"]),
        ) == _file_identity(stat_result)
    except (KeyError, TypeError, ValueError):
        return False


def _cursor_for_handle(handle, source: str, offset: int) -> dict[str, object]:
    stat_result = os.fstat(handle.fileno())
    offset = min(max(0, int(offset)), int(stat_result.st_size))
    head_end = min(offset, CURSOR_HASH_BYTES)
    anchor_start = max(0, offset - CURSOR_HASH_BYTES)
    return {
        "source": source,
        "offset": offset,
        "observed_size": int(stat_result.st_size),
        "mtime_ns": int(stat_result.st_mtime_ns),
        "ctime_ns": int(stat_result.st_ctime_ns),
        "device": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
        "head_sha256": _digest_file_range(handle, 0, head_end),
        "anchor_sha256": _digest_file_range(handle, anchor_start, offset),
    }


def _zero_cursor_for_path(path: Path, source: str) -> dict[str, object] | None:
    try:
        with path.open("rb") as handle:
            return _cursor_for_handle(handle, source, 0)
    except OSError:
        return None


def _cursor_prefix_matches(handle, cursor: dict[str, object], offset: int) -> bool:
    head_end = min(offset, CURSOR_HASH_BYTES)
    anchor_start = max(0, offset - CURSOR_HASH_BYTES)
    return (
        cursor.get("head_sha256") == _digest_file_range(handle, 0, head_end)
        and cursor.get("anchor_sha256") == _digest_file_range(handle, anchor_start, offset)
    )


def scan_jsonl_file_incremental(
    path: Path,
    source: str,
    fallback_path: str,
    previous_cursor: dict[str, object] | None,
) -> tuple[dict[str, Event], dict[str, object] | None, dict[str, int]]:
    """Read only an append-only JSONL tail, resetting safely after replacement/truncation.

    The returned cursor never advances past an incomplete trailing JSON value. If a read
    fails, the previous cursor is retained, so the next run retries the same bytes.
    """
    result: dict[str, Event] = {}
    stats = {
        "scanned_files": 0,
        "skipped_files": 0,
        "new_files": 0,
        "appended_files": 0,
        "reset_files": 0,
        "error_files": 0,
        "bytes_read": 0,
        "malformed_lines": 0,
    }
    try:
        handle = path.open("rb")
    except OSError:
        stats["error_files"] = 1
        return result, previous_cursor, stats

    with handle:
        stat_before = os.fstat(handle.fileno())
        start_offset = 0
        if previous_cursor is None or previous_cursor.get("source") != source:
            stats["new_files"] = 1
        elif not _same_file_identity(previous_cursor, stat_before):
            stats["reset_files"] = 1
        else:
            try:
                previous_offset = int(previous_cursor["offset"])
                previous_mtime = int(previous_cursor["mtime_ns"])
                previous_ctime = int(previous_cursor["ctime_ns"])
            except (KeyError, TypeError, ValueError):
                stats["reset_files"] = 1
            else:
                if stat_before.st_size < previous_offset:
                    stats["reset_files"] = 1
                elif stat_before.st_size == previous_offset:
                    if (
                        stat_before.st_mtime_ns == previous_mtime
                        and stat_before.st_ctime_ns == previous_ctime
                    ):
                        stats["skipped_files"] = 1
                        return result, previous_cursor, stats
                    # Same-length rewrites cannot be distinguished from a touch by size;
                    # rescan conservatively so an in-place replacement is never skipped.
                    stats["reset_files"] = 1
                elif _cursor_prefix_matches(handle, previous_cursor, previous_offset):
                    start_offset = previous_offset
                    stats["appended_files"] = 1
                else:
                    # The inode survived but bytes before the cursor changed (truncate +
                    # regrow or in-place rewrite). Re-read the complete file.
                    stats["reset_files"] = 1

        stats["scanned_files"] = 1
        snapshot_size = int(stat_before.st_size)
        committed_offset = start_offset
        file_cwd = detect_file_cwd(path, fallback_path)
        try:
            handle.seek(start_offset)
            while handle.tell() < snapshot_size:
                remaining = snapshot_size - handle.tell()
                raw_line = handle.readline(remaining)
                if not raw_line:
                    break
                stats["bytes_read"] += len(raw_line)
                line_end = handle.tell()
                has_newline = raw_line.endswith(b"\n")
                stripped = raw_line.decode("utf-8", errors="ignore").strip()
                if not stripped or not stripped.startswith("{"):
                    committed_offset = line_end
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    if not has_newline and line_end == snapshot_size:
                        # A writer may currently be between writing JSON bytes and the
                        # trailing newline. Retry from this exact byte on the next run.
                        break
                    stats["malformed_lines"] += 1
                    committed_offset = line_end
                    continue
                if not isinstance(record, dict):
                    committed_offset = line_end
                    continue
                timestamp = parse_timestamp(record.get("timestamp") or record.get("ts"))
                if timestamp is not None:
                    project_path = cwd_from_record(record) or file_cwd
                    digest_input = f"{source}\0{project_path}\0{stripped}".encode(
                        "utf-8", errors="ignore"
                    )
                    event_id = hashlib.sha256(digest_input).hexdigest()[:24]
                    result[event_id] = Event(
                        event_id=event_id,
                        timestamp=timestamp,
                        source=source,
                        project_path=project_path,
                        event_type=event_type_of(record),
                    )
                # Valid JSON without a trailing newline is a complete record too. If a
                # newline arrives later it is harmless and will be consumed separately.
                committed_offset = line_end
        except OSError:
            stats["error_files"] = 1
            return result, previous_cursor, stats

        stat_after = os.fstat(handle.fileno())
        try:
            path_stat = path.stat()
        except OSError:
            path_stat = None
        if path_stat is None or _file_identity(path_stat) != _file_identity(stat_after):
            # The pathname was replaced while its old inode was open. Archive anything
            # already read, but leave the new pathname at offset zero for the next run.
            stats["reset_files"] += 1
            return result, _zero_cursor_for_path(path, source), stats
        if stat_after.st_size < committed_offset:
            stats["reset_files"] += 1
            return result, _zero_cursor_for_path(path, source), stats
        return result, _cursor_for_handle(handle, source, committed_offset), stats


def scan_jsonl_file(path: Path, source: str, fallback_path: str) -> dict[str, Event]:
    """Compatibility helper for a deliberate full scan of one file."""
    events, _, _ = scan_jsonl_file_incremental(path, source, fallback_path, None)
    return events


def scan_source_events(
    config: dict[str, list[str]],
    claude_roots: list[Path],
    codex_glob: str,
    previous_file_states: dict[str, dict[str, object]] | None = None,
) -> IncrementalScanResult:
    configured_paths = configured_path_candidates(config)
    events: dict[str, Event] = {}
    counts: dict[str, int] = {}
    file_states = dict(previous_file_states or {})
    total_stats: Counter = Counter()
    seen_paths: set[str] = set()

    def scan_path(path: Path, source: str, fallback: str, count_key: str) -> None:
        absolute = Path(os.path.abspath(path))
        state_key = str(absolute)
        if state_key in seen_paths:
            return
        seen_paths.add(state_key)
        total_stats["discovered_files"] += 1
        file_events, cursor, file_stats = scan_jsonl_file_incremental(
            absolute,
            source,
            fallback,
            file_states.get(state_key),
        )
        events.update(file_events)
        counts[count_key] = counts.get(count_key, 0) + len(file_events)
        total_stats.update(file_stats)
        if cursor is not None:
            file_states[state_key] = cursor

    for root in claude_roots:
        paths = sorted((root / "projects").glob("**/*.jsonl")) if (root / "projects").is_dir() else []
        print(f"[origin] 发现 Claude：{root}（{len(paths)} 个 JSONL）", file=sys.stderr, flush=True)
        for path in paths:
            scan_path(
                path,
                "claude",
                claude_fallback_path(path, root, configured_paths),
                str(root),
            )

    codex_paths = [Path(item) for item in sorted(glob.iglob(codex_glob, recursive=True))]
    print(f"[origin] 发现 Codex：{len(codex_paths)} 个 JSONL", file=sys.stderr, flush=True)
    for path in codex_paths:
        scan_path(path, "codex", codex_fallback_path(path), "codex")

    print(
        "[origin] 增量扫描："
        f"读取 {total_stats['scanned_files']} 个，跳过 {total_stats['skipped_files']} 个，"
        f"读取 {total_stats['bytes_read']} 字节",
        file=sys.stderr,
        flush=True,
    )
    return IncrementalScanResult(
        events=events,
        file_states=file_states,
        source_counts=counts,
        stats=dict(total_stats),
    )


def load_origin(path: Path) -> dict[str, Event]:
    if not path.exists():
        return {}
    events = {}
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            cells = split_md_row(line)
            if len(cells) != 5 or cells[0] in ("事件ID", "---"):
                continue
            timestamp = parse_timestamp(cells[1])
            if timestamp is None:
                continue
            event = Event(
                event_id=cells[0],
                timestamp=timestamp,
                source=cells[2],
                project_path=normalize_project_path(cells[3]),
                event_type=cells[4],
            )
            events[event.event_id] = event
    return events


def origin_store_documents(origin_dir: Path) -> list[Path]:
    if origin_dir.is_file():
        return [origin_dir]
    if not origin_dir.exists():
        return []
    return sorted(path for path in origin_dir.glob("*.md") if path.is_file())


def load_origin_store(origin_dir: Path) -> dict[str, Event]:
    """Load and deduplicate the legacy archive plus all natural-week archives."""
    events: dict[str, Event] = {}
    for path in origin_store_documents(origin_dir):
        events.update(load_origin(path))
    return events


def origin_archive_fingerprint(events: dict[str, Event]) -> str:
    """Detect deleted or edited archive rows before trusting source-file cursors."""
    digest = hashlib.sha256()
    for event_id in sorted(events):
        event = events[event_id]
        values = (
            event.event_id,
            event.timestamp.astimezone().isoformat(timespec="milliseconds"),
            event.source,
            event.project_path,
            event.event_type,
        )
        digest.update("\0".join(values).encode("utf-8", errors="ignore"))
        digest.update(b"\n")
    return digest.hexdigest()


def origin_week_start(timestamp: datetime_module.datetime) -> datetime_module.date:
    local_day = timestamp.astimezone().date()
    return local_day - datetime_module.timedelta(days=local_day.weekday())


def weekly_origin_filename(week_start: datetime_module.date) -> str:
    week_end = week_start + datetime_module.timedelta(days=6)
    iso_year, iso_week, _ = week_start.isocalendar()
    return f"{iso_year}-W{iso_week:02d}_{week_start.isoformat()}--{week_end.isoformat()}.md"


def write_origin(path: Path, events: dict[str, Event], roots: list[Path], counts: dict[str, int]) -> None:
    ordered = sorted(
        events.values(),
        key=lambda event: (event.timestamp, event.source, event.project_path, event.event_id),
    )
    now = datetime_module.datetime.now().astimezone().isoformat(timespec="seconds")

    def write(handle) -> None:
        handle.write("# 工作时长原始事件\n\n")
        handle.write(ORIGIN_MARKER + "\n\n")
        handle.write("> 由 work-time 自动生成。每行对应一条去重后的 Claude/Codex JSONL 记录；")
        handle.write("只保留重新计时和按路径分类所需字段，不保存对话正文。\n")
        handle.write("> 事件ID由来源、完整项目路径和原始 JSONL 行计算；备份目录中的相同记录只保留一份。\n")
        handle.write(f"> 更新时间：{now}；事件数：{len(ordered)}。\n")
        if roots:
            handle.write("> Claude 数据目录：" + "、".join(f"`{root}`" for root in roots) + "。\n")
        if counts:
            details = "、".join(f"{name}={count}" for name, count in counts.items())
            handle.write(f"> 本次扫描去重后来源计数：{details}。\n")
        handle.write("\n| 事件ID | 时间戳（ISO 8601，本地时区） | 来源 | 项目路径 | 记录类型 |\n")
        handle.write("|---|---|---|---|---|\n")
        for event in ordered:
            handle.write(md_row((
                event.event_id,
                event.timestamp.astimezone().isoformat(timespec="milliseconds"),
                event.source,
                event.project_path,
                event.event_type,
            )) + "\n")

    atomic_write(path, write)


def write_weekly_origin(
    path: Path,
    events: dict[str, Event],
    week_start: datetime_module.date,
) -> None:
    week_end = week_start + datetime_module.timedelta(days=6)
    ordered = sorted(
        events.values(),
        key=lambda event: (event.timestamp, event.source, event.project_path, event.event_id),
    )
    wrong_week = [event.event_id for event in ordered if origin_week_start(event.timestamp) != week_start]
    if wrong_week:
        raise RuntimeError(f"周归档含有不属于 {week_start} 的事件：{wrong_week[0]}")
    iso_year, iso_week, _ = week_start.isocalendar()
    now = datetime_module.datetime.now().astimezone().isoformat(timespec="seconds")

    def write(handle) -> None:
        handle.write(f"# 工作时长原始事件 · {iso_year}-W{iso_week:02d}\n\n")
        handle.write(WEEKLY_ORIGIN_MARKER + "\n\n")
        handle.write(
            f"> 自然周：{week_start.isoformat()}（周一）至 {week_end.isoformat()}（周日）；"
            f"事件数：{len(ordered)}；更新时间：{now}。\n"
        )
        handle.write(
            "> 由 work-time 增量写入；只保留事件ID、时间戳、来源、完整项目路径和记录类型，不保存对话正文。\n\n"
        )
        handle.write("| 事件ID | 时间戳（ISO 8601，本地时区） | 来源 | 项目路径 | 记录类型 |\n")
        handle.write("|---|---|---|---|---|\n")
        for event in ordered:
            handle.write(md_row((
                event.event_id,
                event.timestamp.astimezone().isoformat(timespec="milliseconds"),
                event.source,
                event.project_path,
                event.event_type,
            )) + "\n")

    atomic_write(path, write)


def write_weekly_origin_additions(
    origin_dir: Path,
    additions: dict[str, Event],
) -> list[Path]:
    if origin_dir.exists() and not origin_dir.is_dir():
        raise RuntimeError(f"originData 必须是目录：{origin_dir}")
    origin_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[datetime_module.date, dict[str, Event]] = defaultdict(dict)
    for event_id, event in additions.items():
        grouped[origin_week_start(event.timestamp)][event_id] = event

    updated_paths = []
    for week_start in sorted(grouped):
        path = origin_dir / weekly_origin_filename(week_start)
        weekly_events = load_origin(path)
        weekly_events.update(grouped[week_start])
        write_weekly_origin(path, weekly_events, week_start)
        updated_paths.append(path)
    return updated_paths


def scan_state_path(origin_dir: Path) -> Path:
    return origin_dir / SCAN_STATE_NAME


def load_scan_state(origin_dir: Path) -> dict[str, object] | None:
    path = scan_state_path(origin_dir)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"[origin] 增量游标损坏，将安全地完整重建：{path}", file=sys.stderr, flush=True)
        return None
    if (
        not isinstance(state, dict)
        or state.get("version") != SCAN_STATE_VERSION
        or not isinstance(state.get("files"), dict)
        or not isinstance(state.get("archive_fingerprint"), str)
    ):
        print(f"[origin] 增量游标版本或结构无效，将安全地完整重建：{path}", file=sys.stderr, flush=True)
        return None
    return state


def scan_state_matches_archive(
    state: dict[str, object] | None,
    events: dict[str, Event],
) -> bool:
    if state is None:
        return False
    return (
        state.get("archive_event_count") == len(events)
        and state.get("archive_fingerprint") == origin_archive_fingerprint(events)
    )


def write_scan_state(
    origin_dir: Path,
    file_states: dict[str, dict[str, object]],
    events: dict[str, Event],
    scan_stats: dict[str, int | str],
) -> None:
    now = datetime_module.datetime.now().astimezone().isoformat(timespec="seconds")
    state = {
        "version": SCAN_STATE_VERSION,
        "updated_at": now,
        "archive_event_count": len(events),
        "archive_fingerprint": origin_archive_fingerprint(events),
        "files": {key: file_states[key] for key in sorted(file_states)},
        "last_scan": scan_stats,
    }

    def write(handle) -> None:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    atomic_write(scan_state_path(origin_dir), write)


@contextlib.contextmanager
def origin_store_lock(origin_dir: Path):
    if origin_dir.exists() and not origin_dir.is_dir():
        raise RuntimeError(f"originData 必须是目录：{origin_dir}")
    origin_dir.mkdir(parents=True, exist_ok=True)
    lock_path = origin_dir / SCAN_LOCK_NAME
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def dedupe_for_timing(events: list[Event]) -> list[Event]:
    unique = {}
    for event in events:
        key = (event.timestamp.isoformat(), event.source, event.project_path)
        unique[key] = event
    return sorted(unique.values(), key=lambda event: (event.timestamp, event.project_path, event.source))


def classify_events(events: list[Event], config: dict[str, list[str]]) -> list[ClassifiedEvent]:
    return [
        ClassifiedEvent(
            timestamp=event.timestamp,
            category=classify_project(event.project_path, config),
            project_path=event.project_path,
            source=event.source,
        )
        for event in dedupe_for_timing(events)
    ]


def main_paths(paths: list[str]) -> tuple[str, ...]:
    counts = Counter(paths)
    return tuple(sorted(counts, key=lambda path: (-counts[path], path)))


def project_folder_name(project_path: str) -> str:
    normalized = normalize_project_path(project_path)
    name = Path(normalized.rstrip("/")).name
    return name or normalized


def format_project_folders(project_paths: tuple[str, ...] | list[str]) -> str:
    names = dict.fromkeys(project_folder_name(path) for path in project_paths)
    return "、".join(names)


def build_segments(
    events: list[ClassifiedEvent],
    gap: datetime_module.timedelta,
    prep: datetime_module.timedelta,
    read: datetime_module.timedelta,
) -> list[Segment]:
    if not events:
        return []
    grouped = {category: [] for category in CATEGORIES}
    for event in events:
        grouped[event.category].append(event)
    result = []
    for category in CATEGORIES:
        ordered = sorted(
            grouped[category],
            key=lambda event: (event.timestamp, event.project_path, event.source),
        )
        if not ordered:
            continue
        raw_segments = []
        current = [ordered[0]]
        previous = ordered[0].timestamp
        for event in ordered[1:]:
            if event.timestamp - previous > gap:
                raw_segments.append(current)
                current = []
            current.append(event)
            previous = event.timestamp
        raw_segments.append(current)

        for raw_segment in raw_segments:
            start = raw_segment[0].timestamp - prep
            end = raw_segment[-1].timestamp + read
            result.append(Segment(
                category=category,
                start=start,
                end=end,
                seconds=int((end - start).total_seconds()),
                project_paths=main_paths([event.project_path for event in raw_segment]),
            ))
    return sorted(
        result,
        key=lambda segment: (segment.start, CATEGORIES.index(segment.category), segment.end),
    )


def segments_by_category(segments: list[Segment]) -> dict[str, list[Segment]]:
    result = {category: [] for category in CATEGORIES}
    for segment in segments:
        result[segment.category].append(segment)
    return result


def segment_detail(segment: Segment) -> dict[str, object]:
    return {
        "date": fmt_date_weekday(segment.start),
        "start": segment.start.strftime("%H:%M"),
        "end": segment.end.strftime("%H:%M"),
        "human": fmt_duration(segment.seconds),
        "seconds": segment.seconds,
        "project_paths": list(segment.project_paths),
    }


def category_stats(segments: list[Segment]) -> dict[str, object]:
    seconds = sum(segment.seconds for segment in segments)
    days = {segment.start.date().isoformat() for segment in segments}
    return {
        "total_seconds": seconds,
        "total_human": fmt_duration(seconds),
        "segments": len(segments),
        "days_worked": len(days),
        "seg_details": [segment_detail(segment) for segment in segments],
    }


def detail_rows_from_section(text: str, heading_prefix: str) -> list[list[str]]:
    rows = []
    for line in section_text(text, heading_prefix).splitlines():
        cells = split_md_row(line)
        if len(cells) == 6 and re.fullmatch(r"\d{4}-\d{2}-\d{2} 周.", cells[0]):
            rows.append(cells)
    return rows


def date_of_row(row: list[str]) -> datetime_module.date:
    return datetime_module.date.fromisoformat(row[0][:10])


def human_duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(?:(\d+) 小时 )?(\d+) 分钟", value)
    if not match:
        raise RuntimeError(f"无法解析台账时长：{value}")
    return (int(match.group(1) or 0) * 60 + int(match.group(2))) * 60


def compact_duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)h(\d{2})m", value)
    if not match:
        raise RuntimeError(f"无法解析周汇总时长：{value}")
    return (int(match.group(1)) * 60 + int(match.group(2))) * 60


def manual_segments_in_window(
    ledger_text: str,
    start: datetime_module.datetime,
    end: datetime_module.datetime,
) -> list[Segment]:
    segments = []
    timezone = start.tzinfo
    for row in detail_rows_from_section(ledger_text, "手动打卡明细"):
        day = date_of_row(row)
        start_time = datetime_module.time.fromisoformat(row[1])
        end_time = datetime_module.time.fromisoformat(row[2])
        started = datetime_module.datetime.combine(day, start_time, tzinfo=timezone)
        ended = datetime_module.datetime.combine(day, end_time, tzinfo=timezone)
        if ended <= started:
            ended += datetime_module.timedelta(days=1)
        if not (start <= started < end):
            continue
        content = row[4].strip()
        label = f"手动打卡：{content}" if content else "手动打卡"
        segments.append(Segment(
            category="planB项目",
            start=started,
            end=ended,
            seconds=human_duration_seconds(row[3]),
            project_paths=(label,),
        ))
    return segments


def segment_to_row(segment: Segment, note: str = "") -> list[str]:
    return [
        fmt_date_weekday(segment.start),
        segment.start.strftime("%H:%M"),
        segment.end.strftime("%H:%M"),
        fmt_duration(segment.seconds),
        format_project_folders(segment.project_paths),
        note,
    ]


def render_detail_section(category: str, rows: list[list[str]]) -> str:
    lines = [
        f"## {DETAIL_HEADINGS[category]}",
        "",
        "> 事件先按项目路径分类，再在本分类内独立合并连续段；「备注」可手填产出或状态，自动更新时按“日期+起”保留。",
        "",
        "| 日期 | 起 | 止 | 时长 | 项目 | 备注(产出/状态) |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(md_row(row) for row in sorted(rows, key=lambda row: (row[0][:10], row[1])))
    return "\n".join(lines)


def row_notes(text: str) -> dict[tuple[str, str, str], str]:
    headings = (
        ("工作段明细", "*"),
        ("planB 工作段明细", "planB项目"),
        (DETAIL_HEADINGS["工作项目"], "工作项目"),
        (DETAIL_HEADINGS["planB项目"], "planB项目"),
        (DETAIL_HEADINGS["其他项目"], "其他项目"),
    )
    notes: dict[tuple[str, str, str], str] = {}
    for heading, category in headings:
        for row in detail_rows_from_section(text, heading):
            note = row[5].strip()
            key = (category, row[0], row[1])
            if note and note not in notes.get(key, ""):
                notes[key] = "、".join(filter(None, (notes.get(key), note)))
    return notes


def dates_in_window(start: datetime_module.datetime, end: datetime_module.datetime) -> set[datetime_module.date]:
    if end <= start:
        return set()
    first = start.date()
    last = (end - datetime_module.timedelta(microseconds=1)).date()
    return {first + datetime_module.timedelta(days=index) for index in range((last - first).days + 1)}


def replace_detail_tables(
    ledger_text: str,
    categorized: dict[str, list[Segment]],
    start: datetime_module.datetime,
    end: datetime_module.datetime,
) -> tuple[str, dict[str, list[list[str]]], dict[tuple[str, str, str], int]]:
    replacement_dates = dates_in_window(start, end)
    for segment in (segment for values in categorized.values() for segment in values):
        replacement_dates.add(segment.start.date())
    notes = row_notes(ledger_text)
    used_note_keys = set()
    final_rows: dict[str, list[list[str]]] = {category: [] for category in CATEGORIES}
    new_segment_counts = Counter(
        (fmt_date_weekday(segment.start), segment.start.strftime("%H:%M"))
        for values in categorized.values()
        for segment in values
    )

    for category in CATEGORIES:
        for row in detail_rows_from_section(ledger_text, DETAIL_HEADINGS[category]):
            if date_of_row(row) not in replacement_dates:
                final_rows[category].append(row)

    legacy_all = detail_rows_from_section(ledger_text, "工作段明细")
    for row in legacy_all:
        if date_of_row(row) not in replacement_dates:
            historical = row.copy()
            historical[4] = f"历史未分类：{row[4]}"
            final_rows["其他项目"].append(historical)

    exact_seconds = {}
    for category in CATEGORIES:
        for segment in categorized[category]:
            date_start = (fmt_date_weekday(segment.start), segment.start.strftime("%H:%M"))
            exact_note_key = (category, *date_start)
            legacy_note_key = ("*", *date_start)
            note_key = exact_note_key if exact_note_key in notes else None
            if note_key is None and legacy_note_key in notes and new_segment_counts[date_start] == 1:
                note_key = legacy_note_key
            note = notes.get(note_key, "") if note_key else ""
            if note_key:
                used_note_keys.add(note_key)
            row = segment_to_row(segment, note)
            final_rows[category].append(row)
            exact_seconds[(category, row[0], row[1])] = segment.seconds

    orphan_notes = {
        key: note
        for key, note in notes.items()
        if datetime_module.date.fromisoformat(key[1][:10]) in replacement_dates
        and key not in used_note_keys
    }
    if orphan_notes:
        sample = "、".join(f"{category} {date} {start}" for category, date, start in list(orphan_notes)[:5])
        raise RuntimeError(f"分类后有无法安全匹配的手填备注，已停止写入：{sample}")

    for heading in (
        "工作段明细",
        "planB 工作段明细",
        DETAIL_HEADINGS["工作项目"],
        DETAIL_HEADINGS["planB项目"],
        DETAIL_HEADINGS["其他项目"],
    ):
        ledger_text = remove_section(ledger_text, heading)

    detail_text = "\n\n".join(
        render_detail_section(category, final_rows[category]) for category in CATEGORIES
    ) + "\n\n"
    insertion = re.search(r"(?m)^## 周汇总", ledger_text)
    if not insertion:
        insertion = re.search(r"(?m)^## 手动打卡明细", ledger_text)
    index = insertion.start() if insertion else len(ledger_text)
    ledger_text = ledger_text[:index].rstrip() + "\n\n" + detail_text + ledger_text[index:].lstrip("\n")
    return ledger_text, final_rows, exact_seconds


def week_range(day: datetime_module.date) -> tuple[datetime_module.date, datetime_module.date]:
    monday = day - datetime_module.timedelta(days=day.weekday())
    return monday, monday + datetime_module.timedelta(days=6)


def week_key(day: datetime_module.date) -> str:
    monday, sunday = week_range(day)
    return f"{monday.isoformat()} ~ {sunday.isoformat()}"


def weekly_rows_from_section(ledger_text: str) -> list[list[str]]:
    rows = []
    for line in section_text(ledger_text, "周汇总").splitlines():
        cells = split_md_row(line)
        if len(cells) == 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2} ~ \d{4}-\d{2}-\d{2}", cells[0]):
            rows.append(cells)
    return rows


def render_weekly_rows(rows: list[list[object]]) -> str:
    lines = [
        "## 周汇总（自动记录 + 手动打卡）",
        "",
        WEEKLY_MANUAL_NOTE,
        "",
        "| 自然周（周一~周日） | 工作项目时长 | 工作段数 | planB项目时长 | planB段数 | 其他项目时长 | 其他段数 | 分类合计 | 活跃天数 | 更新时间 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(md_row(row) for row in sorted(rows, key=lambda row: str(row[0])))
    return "\n".join(lines)


def render_weekly_summary(
    detail_rows: dict[str, list[list[str]]],
    exact_seconds: dict[tuple[str, str, str], int],
    manual_rows: list[list[str]] | None = None,
) -> str:
    totals: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "seconds": {category: 0 for category in CATEGORIES},
            "segments": {category: 0 for category in CATEGORIES},
            "days": set(),
        }
    )
    for category in CATEGORIES:
        for row in detail_rows[category]:
            day = date_of_row(row)
            key = week_key(day)
            seconds = exact_seconds.get((category, row[0], row[1]), human_duration_seconds(row[3]))
            totals[key]["seconds"][category] += seconds
            totals[key]["segments"][category] += 1
            totals[key]["days"].add(day.isoformat())

    for row in manual_rows or []:
        day = date_of_row(row)
        key = week_key(day)
        totals[key]["seconds"]["planB项目"] += human_duration_seconds(row[3])
        totals[key]["segments"]["planB项目"] += 1
        totals[key]["days"].add(day.isoformat())

    rows = []
    today = datetime_module.date.today().isoformat()
    for key in sorted(totals):
        item = totals[key]
        seconds = item["seconds"]
        segments = item["segments"]
        total_seconds = sum(seconds.values())
        rows.append([
            key,
            fmt_compact_duration(seconds["工作项目"]),
            segments["工作项目"],
            fmt_compact_duration(seconds["planB项目"]),
            segments["planB项目"],
            fmt_compact_duration(seconds["其他项目"]),
            segments["其他项目"],
            fmt_compact_duration(total_seconds),
            len(item["days"]),
            today,
        ])
    return render_weekly_rows(rows)


def add_manual_row_to_weekly(ledger_text: str, manual_row: list[str]) -> str:
    """Preserve existing automatic totals and add a newly completed manual PlanB segment."""
    weekly_section = section_text(ledger_text, "周汇总")
    summaries = {row[0]: row.copy() for row in weekly_rows_from_section(ledger_text)}
    original_keys = set(summaries)

    # If a week has detail rows but no summary yet, build a minute-level fallback row.
    for category in CATEGORIES:
        for row in detail_rows_from_section(ledger_text, DETAIL_HEADINGS[category]):
            day = date_of_row(row)
            key = week_key(day)
            if key in original_keys:
                continue
            summary = summaries.setdefault(
                key,
                [key, "0h00m", 0, "0h00m", 0, "0h00m", 0, "0h00m", 0, ""],
            )
            duration_index = {"工作项目": 1, "planB项目": 3, "其他项目": 5}[category]
            segment_index = duration_index + 1
            seconds = compact_duration_seconds(summary[duration_index]) + human_duration_seconds(row[3])
            summary[duration_index] = fmt_compact_duration(seconds)
            summary[segment_index] = int(summary[segment_index]) + 1
            total = sum(compact_duration_seconds(summary[index]) for index in (1, 3, 5))
            summary[7] = fmt_compact_duration(total)

    # Before this feature existed, old manual rows were not included. Migrate them once.
    rows_to_add = [manual_row]
    if WEEKLY_MANUAL_NOTE not in weekly_section:
        rows_to_add = detail_rows_from_section(ledger_text, "手动打卡明细") + rows_to_add

    today = datetime_module.date.today().isoformat()
    for row in rows_to_add:
        day = date_of_row(row)
        key = week_key(day)
        summary = summaries.setdefault(
            key,
            [key, "0h00m", 0, "0h00m", 0, "0h00m", 0, "0h00m", 0, today],
        )
        seconds = human_duration_seconds(row[3])
        summary[3] = fmt_compact_duration(compact_duration_seconds(str(summary[3])) + seconds)
        summary[4] = int(summary[4]) + 1
        summary[7] = fmt_compact_duration(compact_duration_seconds(str(summary[7])) + seconds)
        summary[9] = today

    days_by_week: dict[str, set[str]] = defaultdict(set)
    headings = (*DETAIL_HEADINGS.values(), "手动打卡明细")
    for heading in headings:
        for row in detail_rows_from_section(ledger_text, heading):
            day = date_of_row(row)
            days_by_week[week_key(day)].add(day.isoformat())
    new_day = date_of_row(manual_row)
    days_by_week[week_key(new_day)].add(new_day.isoformat())
    for key, days in days_by_week.items():
        if key in summaries:
            summaries[key][8] = len(days)

    return replace_section(ledger_text, "周汇总", render_weekly_rows(list(summaries.values())))


def archive_ledger(
    ledger_path: Path,
    config: dict[str, list[str]],
    categorized: dict[str, list[Segment]],
    start: datetime_module.datetime,
    end: datetime_module.datetime,
) -> None:
    text = ledger_path.read_text(encoding="utf-8")
    text = text.replace(
        "# 工作时长台账（用 AI 工作 = plan B / 编程 / build）",
        "# 工作时长台账（按项目路径分类）",
    )
    text = text.replace("每段前加5分钟准备、后加10分钟阅读", "每段前加5分钟准备、后加5分钟阅读")
    text = text.replace("项目 = 对话所在目录末段名。", "项目按完整工作目录路径分类。")
    text = normalize_config_section(text, config)
    text, detail_rows, exact_seconds = replace_detail_tables(text, categorized, start, end)
    manual_rows = detail_rows_from_section(text, "手动打卡明细")
    weekly = render_weekly_summary(detail_rows, exact_seconds, manual_rows)
    text = replace_section(text, "周汇总", weekly)

    def write(handle) -> None:
        handle.write(text.rstrip() + "\n")

    atomic_write(ledger_path, write)


def ledger_date_range(ledger_text: str) -> tuple[datetime_module.date, datetime_module.date] | None:
    dates = []
    for heading in (
        "工作段明细",
        "planB 工作段明细",
        *DETAIL_HEADINGS.values(),
    ):
        dates.extend(date_of_row(row) for row in detail_rows_from_section(ledger_text, heading))
    if not dates:
        return None
    return min(dates), max(dates) + datetime_module.timedelta(days=1)


def determine_window(args, events: dict[str, Event], ledger_text: str):
    now = parse_timestamp(args.now) if args.now else datetime_module.datetime.now().astimezone()
    if now is None:
        raise RuntimeError("--now 不是有效的 ISO 8601 时间")
    timezone = now.tzinfo
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if args.reclassify_ledger:
        date_range = ledger_date_range(ledger_text)
        if not date_range:
            raise RuntimeError("台账中没有可重新分类的时间段")
        start_date, end_date = date_range
        return (
            datetime_module.datetime.combine(start_date, datetime_module.time.min, tzinfo=timezone),
            datetime_module.datetime.combine(end_date, datetime_module.time.min, tzinfo=timezone),
            f"重新分类台账 {start_date} → {end_date - datetime_module.timedelta(days=1)}",
        )
    if args.all:
        if not events:
            raise RuntimeError("原始事件库为空，无法使用 --all")
        timestamps = [event.timestamp for event in events.values()]
        start_date = min(timestamps).date()
        end_date = max(timestamps).date() + datetime_module.timedelta(days=1)
        return (
            datetime_module.datetime.combine(start_date, datetime_module.time.min, tzinfo=timezone),
            datetime_module.datetime.combine(end_date, datetime_module.time.min, tzinfo=timezone),
            f"全部原始事件 {start_date} → {end_date - datetime_module.timedelta(days=1)}",
        )
    if args.start or args.end:
        if not (args.start and args.end):
            raise RuntimeError("--start 和 --end 必须一起使用；--end 为不包含的结束日期")
        start_date = datetime_module.date.fromisoformat(args.start)
        end_date = datetime_module.date.fromisoformat(args.end)
        if end_date <= start_date:
            raise RuntimeError("--end 必须晚于 --start")
        return (
            datetime_module.datetime.combine(start_date, datetime_module.time.min, tzinfo=timezone),
            datetime_module.datetime.combine(end_date, datetime_module.time.min, tzinfo=timezone),
            f"指定日期 {start_date} → {end_date - datetime_module.timedelta(days=1)}",
        )
    if args.days is not None:
        end = today_start
        start = end - datetime_module.timedelta(days=args.days)
        return start, end, f"过去 {args.days} 天（截止昨天 24:00）"
    start = today_start - datetime_module.timedelta(days=today_start.weekday())
    return start, now, "本周（周一 00:00 → 现在）"


def render_report(
    label: str,
    categorized: dict[str, list[Segment]],
    origin_count: int,
    new_origin_count: int,
    gap_min: int,
    prep_min: int,
    read_min: int,
) -> list[str]:
    lines = [
        f"# work-time · {label}",
        "",
        f"> 口径：先按完整项目路径区分工作、Plan B 与其他；再在每一类内部独立按相邻间隔 ≤{gap_min} 分钟合并连续段，每个分类段前加 {prep_min} 分钟准备、后加 {read_min} 分钟阅读。不同分类可在墙钟时间上重叠，互不吞并。",
        "> 通过 `start` 开启并完成的手动打卡按精确时长计入 planB项目，不加补偿。",
        f"> 原始事件库共 {origin_count} 条，本次新增 {new_origin_count} 条。观测用，不设目标、不评判。",
        "",
    ]
    stats = {category: category_stats(categorized[category]) for category in CATEGORIES}
    icons = {"工作项目": "💼", "planB项目": "🅱️", "其他项目": "🧩"}
    for category in CATEGORIES:
        item = stats[category]
        lines.append(
            f"- {icons[category]} **{category}：{item['total_human']}**（{item['segments']} 段，覆盖 {item['days_worked']} 天）"
        )
    lines.extend(["", "## 按天", "| 日期 | 工作项目 | planB项目 | 其他项目 |", "|---|---|---|---|"])
    daily = defaultdict(lambda: {category: 0 for category in CATEGORIES})
    for category in CATEGORIES:
        for segment in categorized[category]:
            daily[segment.start.date()][category] += segment.seconds
    for day in sorted(daily):
        lines.append(md_row((
            fmt_date_weekday(day),
            fmt_duration(daily[day]["工作项目"]),
            fmt_duration(daily[day]["planB项目"]),
            fmt_duration(daily[day]["其他项目"]),
        )))
    for category in CATEGORIES:
        lines.extend(["", f"## {DETAIL_HEADINGS[category]}"])
        lines.append("| 段 | 日期 | 起 | 止 | 时长 | 项目 |")
        lines.append("|---|---|---|---|---|---|")
        for index, segment in enumerate(categorized[category], 1):
            lines.append(md_row((
                index,
                fmt_date_weekday(segment.start),
                segment.start.strftime("%H:%M"),
                segment.end.strftime("%H:%M"),
                fmt_duration(segment.seconds),
                format_project_folders(segment.project_paths),
            )))
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--days", type=int)
    window.add_argument("--all", action="store_true")
    window.add_argument("--reclassify-ledger", action="store_true")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--gap-min", type=int, default=30)
    parser.add_argument("--prep-min", type=int, default=5)
    parser.add_argument("--read-min", type=int, default=5)
    parser.add_argument("--from-origin", action="store_true", help="不扫描日志，只复用 originData")
    parser.add_argument(
        "--rebuild-scan-state",
        action="store_true",
        help="忽略增量游标，完整扫描一次并重建游标（故障恢复用）",
    )
    parser.add_argument("--no-archive", action="store_true")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--origin", type=Path, default=DEFAULT_ORIGIN)
    parser.add_argument("--claude-root", action="append")
    parser.add_argument("--codex-glob", default=DEFAULT_CODEX_GLOB)
    parser.add_argument("--now", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.days is not None and args.days <= 0:
        raise SystemExit("--days 必须大于 0")
    if args.from_origin and args.rebuild_scan_state:
        raise SystemExit("--from-origin 与 --rebuild-scan-state 不能同时使用")
    ledger_text = args.ledger.read_text(encoding="utf-8")
    config = parse_project_config(ledger_text)
    roots = discover_claude_roots(args.claude_root)
    source_counts: dict[str, int] = {}
    scan_stats: dict[str, int | str] = {"mode": "from-origin"}
    updated_origin_files: list[Path] = []
    with origin_store_lock(args.origin):
        cached_events = load_origin_store(args.origin)
        if args.from_origin:
            if not cached_events:
                raise SystemExit(f"原始事件库不存在或为空：{args.origin}")
            events = cached_events
            new_origin_count = 0
        else:
            state = load_scan_state(args.origin)
            state_matches = scan_state_matches_archive(state, cached_events)
            if args.rebuild_scan_state:
                scan_mode = "forced-rebuild"
                previous_file_states: dict[str, dict[str, object]] = {}
            elif state_matches:
                scan_mode = "incremental"
                state_files = state.get("files", {}) if state else {}
                previous_file_states = {
                    str(path): cursor
                    for path, cursor in state_files.items()
                    if isinstance(path, str) and isinstance(cursor, dict)
                }
            else:
                scan_mode = "bootstrap"
                previous_file_states = {}
                if state is not None:
                    print(
                        "[origin] 归档内容与增量游标不一致，将完整扫描一次以避免漏记。",
                        file=sys.stderr,
                        flush=True,
                    )

            scan_result = scan_source_events(
                config,
                roots,
                args.codex_glob,
                previous_file_states,
            )
            source_counts = scan_result.source_counts
            events = dict(cached_events)
            additions = {
                event_id: event
                for event_id, event in scan_result.events.items()
                if event_id not in events
            }
            events.update(additions)
            new_origin_count = len(additions)
            # Crash safety: archive rows are committed before cursors. A failure between
            # the two can only cause a safe duplicate scan on the next run, never a skip.
            updated_origin_files = write_weekly_origin_additions(args.origin, additions)
            scan_stats = dict(scan_result.stats)
            scan_stats["mode"] = scan_mode
            write_scan_state(
                args.origin,
                scan_result.file_states,
                events,
                scan_stats,
            )

    start, end, label = determine_window(args, events, ledger_text)
    window_events = [event for event in events.values() if start <= event.timestamp < end]
    classified = classify_events(window_events, config)
    segments = build_segments(
        classified,
        datetime_module.timedelta(minutes=args.gap_min),
        datetime_module.timedelta(minutes=args.prep_min),
        datetime_module.timedelta(minutes=args.read_min),
    )
    categorized = segments_by_category(segments)
    report_categorized = {category: list(categorized[category]) for category in CATEGORIES}
    report_categorized["planB项目"].extend(manual_segments_in_window(ledger_text, start, end))
    report_categorized["planB项目"].sort(key=lambda segment: segment.start)
    lines = render_report(
        label,
        report_categorized,
        len(events),
        new_origin_count,
        args.gap_min,
        args.prep_min,
        args.read_min,
    )
    if not args.from_origin:
        lines.extend([
            "",
            (
                f"> originData {scan_stats['mode']}：发现 {scan_stats.get('discovered_files', 0)} 个日志文件，"
                f"读取 {scan_stats.get('scanned_files', 0)} 个、跳过 {scan_stats.get('skipped_files', 0)} 个，"
                f"新增 {new_origin_count} 条事件；更新 {len(updated_origin_files)} 个自然周文件。"
            ),
        ])
    if not args.no_archive:
        archive_ledger(args.ledger, config, categorized, start, end)
        lines.extend(["", f"> 已归档到 `{args.ledger}`。"])
    signals = {
        "window_local": [start.isoformat(), end.isoformat()],
        "origin": str(args.origin),
        "origin_events": len(events),
        "new_origin_events": new_origin_count,
        "updated_origin_files": [str(path) for path in updated_origin_files],
        "scan_state": str(scan_state_path(args.origin)),
        "scan": scan_stats,
        "source_counts": source_counts,
        "claude_roots": [str(root) for root in roots],
        "gap_min": args.gap_min,
        "prep_min": args.prep_min,
        "read_min": args.read_min,
        "project_paths": config,
        "categories": {category: category_stats(report_categorized[category]) for category in CATEGORIES},
    }
    lines.extend(["", "SIGNALS: " + json.dumps(signals, ensure_ascii=False)])
    print("\n".join(lines))


if __name__ == "__main__":
    main()
