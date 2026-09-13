import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "work_time.py"
SPEC = importlib.util.spec_from_file_location("work_time", SCRIPT)
work_time = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = work_time
SPEC.loader.exec_module(work_time)

MANUAL_SCRIPT = Path(__file__).parents[1] / "scripts" / "manual_clock.py"
MANUAL_SPEC = importlib.util.spec_from_file_location("manual_clock", MANUAL_SCRIPT)
manual_clock = importlib.util.module_from_spec(MANUAL_SPEC)
sys.modules[MANUAL_SPEC.name] = manual_clock
MANUAL_SPEC.loader.exec_module(manual_clock)


class WorkTimeTests(unittest.TestCase):
    def test_project_path_classification_supports_descendants_and_globs(self):
        config = {
            "工作项目": ["/Users/aaron/code/tron*"],
            "planB项目": ["/Users/aaron/workspace/llmWikis"],
        }
        self.assertEqual(
            work_time.classify_project("/Users/aaron/code/tronlink-extension/packages/app", config),
            "工作项目",
        )
        self.assertEqual(
            work_time.classify_project("/Users/aaron/workspace/llmWikis/improveWiki", config),
            "planB项目",
        )
        self.assertEqual(
            work_time.classify_project("/Users/aaron/Documents/notes", config),
            "其他项目",
        )

    def test_project_display_uses_unique_folder_names_and_chinese_separator(self):
        self.assertEqual(
            work_time.format_project_folders((
                "/Users/aaron/code/tronlink-extension-pro",
                "/private/tmp/tronlink-extension-pro",
                "/Users/aaron/code/tronlink-extension-pro/script/security",
            )),
            "tronlink-extension-pro、security",
        )

    def test_categories_are_segmented_independently_with_five_plus_five_padding(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        events = [
            work_time.ClassifiedEvent(
                dt.datetime(2026, 8, 10, 10, 0, tzinfo=timezone),
                "工作项目",
                "/work",
                "claude",
            ),
            work_time.ClassifiedEvent(
                dt.datetime(2026, 8, 10, 10, 10, tzinfo=timezone),
                "planB项目",
                "/planb",
                "codex",
            ),
            work_time.ClassifiedEvent(
                dt.datetime(2026, 8, 10, 10, 20, tzinfo=timezone),
                "工作项目",
                "/work",
                "claude",
            ),
        ]
        segments = work_time.build_segments(
            events,
            dt.timedelta(minutes=30),
            dt.timedelta(minutes=5),
            dt.timedelta(minutes=5),
        )
        self.assertEqual(
            [segment.category for segment in segments],
            ["工作项目", "planB项目"],
        )
        self.assertEqual([segment.seconds for segment in segments], [1800, 600])
        self.assertEqual(segments[0].start.strftime("%H:%M"), "09:55")
        self.assertEqual(segments[0].end.strftime("%H:%M"), "10:25")
        self.assertEqual(segments[1].start.strftime("%H:%M"), "10:05")
        self.assertEqual(segments[1].end.strftime("%H:%M"), "10:15")

    def test_path_classification_precedes_category_local_merging(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        config = {"工作项目": ["/work"], "planB项目": ["/planb"]}
        events = [
            work_time.Event(
                "work-1",
                dt.datetime(2026, 8, 10, 10, 0, tzinfo=timezone),
                "claude",
                "/work/app",
                "assistant",
            ),
            work_time.Event(
                "planb-1",
                dt.datetime(2026, 8, 10, 10, 10, tzinfo=timezone),
                "codex",
                "/planb/lab",
                "assistant",
            ),
            work_time.Event(
                "work-2",
                dt.datetime(2026, 8, 10, 10, 20, tzinfo=timezone),
                "claude",
                "/work/app",
                "assistant",
            ),
        ]

        classified = work_time.classify_events(events, config)
        segments = work_time.build_segments(
            classified,
            dt.timedelta(minutes=30),
            dt.timedelta(minutes=5),
            dt.timedelta(minutes=5),
        )
        categorized = work_time.segments_by_category(segments)

        self.assertEqual(work_time.category_stats(categorized["工作项目"])["total_seconds"], 1800)
        self.assertEqual(work_time.category_stats(categorized["planB项目"])["total_seconds"], 600)
        self.assertEqual(work_time.category_stats(categorized["其他项目"])["total_seconds"], 0)

    def test_other_category_event_does_not_bridge_work_segments(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        events = [
            work_time.ClassifiedEvent(
                dt.datetime(2026, 8, 10, 10, 0, tzinfo=timezone),
                "工作项目",
                "/work",
                "claude",
            ),
            work_time.ClassifiedEvent(
                dt.datetime(2026, 8, 10, 10, 20, tzinfo=timezone),
                "planB项目",
                "/planb",
                "codex",
            ),
            work_time.ClassifiedEvent(
                dt.datetime(2026, 8, 10, 10, 40, tzinfo=timezone),
                "工作项目",
                "/work",
                "claude",
            ),
        ]

        segments = work_time.build_segments(
            events,
            dt.timedelta(minutes=30),
            dt.timedelta(minutes=5),
            dt.timedelta(minutes=5),
        )

        self.assertEqual(
            [(segment.category, segment.start.strftime("%H:%M"), segment.end.strftime("%H:%M")) for segment in segments],
            [
                ("工作项目", "09:55", "10:05"),
                ("planB项目", "10:15", "10:25"),
                ("工作项目", "10:35", "10:45"),
            ],
        )

    def test_origin_round_trip_keeps_only_timing_fields(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        event = work_time.Event(
            "abc123",
            dt.datetime(2026, 8, 10, 10, 0, 0, 123000, tzinfo=timezone),
            "claude",
            "/Users/aaron/workspace/llmWikis",
            "assistant",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "origin.md"
            work_time.write_origin(path, {event.event_id: event}, [], {})
            loaded = work_time.load_origin(path)
        self.assertEqual(loaded, {event.event_id: event})

    def test_weekly_origin_files_use_iso_week_and_explicit_date_range(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        events = {
            "year-end": work_time.Event(
                "year-end",
                dt.datetime(2025, 12, 31, 23, 0, tzinfo=timezone),
                "codex",
                "/planb",
                "event_msg",
            ),
            "next-week": work_time.Event(
                "next-week",
                dt.datetime(2026, 1, 5, 9, 0, tzinfo=timezone),
                "claude",
                "/work",
                "assistant",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            origin_dir = Path(directory) / "origin"
            updated = work_time.write_weekly_origin_additions(origin_dir, events)
            names = [path.name for path in updated]
            loaded = work_time.load_origin_store(origin_dir)

        self.assertEqual(
            names,
            [
                "2026-W01_2025-12-29--2026-01-04.md",
                "2026-W02_2026-01-05--2026-01-11.md",
            ],
        )
        self.assertEqual(loaded, events)

    def test_incremental_scan_skips_unchanged_file_and_reads_only_append(self):
        timezone = dt.timezone.utc
        config = {"工作项目": ["/work"], "planB项目": ["/planb"]}
        first = json.dumps({
            "timestamp": dt.datetime(2026, 8, 21, 1, 0, tzinfo=timezone).isoformat(),
            "cwd": "/work/app",
            "type": "assistant",
        }) + "\n"
        second = json.dumps({
            "timestamp": dt.datetime(2026, 8, 21, 1, 5, tzinfo=timezone).isoformat(),
            "cwd": "/work/app",
            "type": "user",
        }) + "\n"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".claude"
            source = root / "projects" / "-work-app" / "session.jsonl"
            source.parent.mkdir(parents=True)
            source.write_text(first, encoding="utf-8")
            empty_codex_glob = str(Path(directory) / "codex" / "**" / "*.jsonl")

            initial = work_time.scan_source_events(config, [root], empty_codex_glob, {})
            unchanged = work_time.scan_source_events(
                config,
                [root],
                empty_codex_glob,
                initial.file_states,
            )
            with source.open("a", encoding="utf-8") as handle:
                handle.write(second)
            appended = work_time.scan_source_events(
                config,
                [root],
                empty_codex_glob,
                unchanged.file_states,
            )

        self.assertEqual(len(initial.events), 1)
        self.assertEqual(unchanged.events, {})
        self.assertEqual(unchanged.stats["skipped_files"], 1)
        self.assertEqual(unchanged.stats.get("bytes_read", 0), 0)
        self.assertEqual(len(appended.events), 1)
        self.assertEqual(appended.stats["appended_files"], 1)
        self.assertEqual(appended.stats["bytes_read"], len(second.encode("utf-8")))

    def test_incremental_scan_retries_incomplete_trailing_json(self):
        record = json.dumps({
            "timestamp": "2026-08-21T02:00:00+00:00",
            "cwd": "/planb/lab",
            "type": "assistant",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.jsonl"
            path.write_text(record[:-1], encoding="utf-8")
            first_events, first_cursor, first_stats = work_time.scan_jsonl_file_incremental(
                path,
                "claude",
                "/fallback",
                None,
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write("}\n")
            second_events, second_cursor, second_stats = work_time.scan_jsonl_file_incremental(
                path,
                "claude",
                "/fallback",
                first_cursor,
            )

        self.assertEqual(first_events, {})
        self.assertEqual(first_cursor["offset"], 0)
        self.assertGreater(first_stats["bytes_read"], 0)
        self.assertEqual(len(second_events), 1)
        self.assertGreater(second_cursor["offset"], first_cursor["offset"])
        self.assertEqual(second_stats["malformed_lines"], 0)

    def test_incremental_scan_resets_after_same_length_in_place_rewrite(self):
        first = '{"timestamp":"2026-08-21T03:00:00+00:00","cwd":"/work/a","type":"user"}\n'
        second = '{"timestamp":"2026-08-21T04:00:00+00:00","cwd":"/work/b","type":"user"}\n'
        self.assertEqual(len(first), len(second))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rewritten.jsonl"
            path.write_text(first, encoding="utf-8")
            first_events, cursor, _ = work_time.scan_jsonl_file_incremental(
                path,
                "codex",
                "/fallback",
                None,
            )
            previous_mtime = path.stat().st_mtime_ns
            path.write_text(second, encoding="utf-8")
            os.utime(path, ns=(path.stat().st_atime_ns, previous_mtime + 1_000_000_000))
            second_events, _, stats = work_time.scan_jsonl_file_incremental(
                path,
                "codex",
                "/fallback",
                cursor,
            )

        self.assertEqual(len(first_events), 1)
        self.assertEqual(len(second_events), 1)
        self.assertEqual(next(iter(second_events.values())).project_path, "/work/b")
        self.assertEqual(stats["reset_files"], 1)

    def test_scan_state_rejects_deleted_or_edited_archive_rows(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        event = work_time.Event(
            "kept",
            dt.datetime(2026, 8, 21, 10, 0, tzinfo=timezone),
            "claude",
            "/work",
            "assistant",
        )
        with tempfile.TemporaryDirectory() as directory:
            origin_dir = Path(directory) / "origin"
            work_time.write_weekly_origin_additions(origin_dir, {event.event_id: event})
            events = work_time.load_origin_store(origin_dir)
            work_time.write_scan_state(origin_dir, {}, events, {"mode": "bootstrap"})
            state = work_time.load_scan_state(origin_dir)
            self.assertTrue(work_time.scan_state_matches_archive(state, events))

            weekly_path = next(origin_dir.glob("*.md"))
            weekly_path.write_text("# accidentally truncated\n", encoding="utf-8")
            truncated_events = work_time.load_origin_store(origin_dir)

        self.assertFalse(work_time.scan_state_matches_archive(state, truncated_events))

    def test_archive_migrates_three_time_tables_preserves_project_directory_and_exact_note(self):
        ledger = """# 工作时长台账（用 AI 工作 = plan B / 编程 / build)

## 项目目录
### 工作项目
- `/work`

### planB 项目
- `/planb`

## 工作段明细（旧）

| 日期 | 起 | 止 | 时长 | 项目 | 备注(产出/状态) |
|---|---|---|---|---|---|
| 2026-08-10 周一 | 09:55 | 10:05 | 10 分钟 | old | 保留备注 |

## planB 工作段明细（旧）

| 日期 | 起 | 止 | 时长 | 项目 | 备注(产出/状态) |
|---|---|---|---|---|---|

## 周汇总（自动统计）

| 自然周（周一~周日） | 全部时长 |
|---|---|

## 手动打卡明细（保留）

| 日期 | 起 | 止 | 时长 | 内容 | 备注 |
|---|---|---|---|---|---|
| 2026-08-10 周一 | 10:30 | 11:00 | 30 分钟 | 阅读 |  |
"""
        timezone = dt.timezone(dt.timedelta(hours=8))
        segment = work_time.Segment(
            "工作项目",
            dt.datetime(2026, 8, 10, 9, 55, tzinfo=timezone),
            dt.datetime(2026, 8, 10, 10, 5, tzinfo=timezone),
            600,
            ("/work", "/work/subdir"),
        )
        categorized = {category: [] for category in work_time.CATEGORIES}
        categorized["工作项目"].append(segment)
        config = {"工作项目": ["/work"], "planB项目": ["/planb"]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.md"
            path.write_text(ledger, encoding="utf-8")
            work_time.archive_ledger(
                path,
                config,
                categorized,
                dt.datetime(2026, 8, 10, tzinfo=timezone),
                dt.datetime(2026, 8, 11, tzinfo=timezone),
            )
            updated = path.read_text(encoding="utf-8")
        self.assertNotIn("## 工作段明细", updated)
        self.assertNotIn("## planB 工作段明细", updated)
        self.assertIn("## 项目目录\n### 工作项目\n- `/work`", updated)
        self.assertIn("### planB 项目\n- `/planb`", updated)
        self.assertNotIn("## 项目分类", updated)
        self.assertIn("## 工作项目时间段", updated)
        self.assertIn("## planB项目时间段", updated)
        self.assertIn("## 其他项目时间段", updated)
        self.assertIn("| 10 分钟 | work、subdir | 保留备注 |", updated)
        self.assertNotIn("<br>", updated)
        self.assertIn("保留备注", updated)
        self.assertIn(
            "| 2026-08-10 ~ 2026-08-16 | 0h10m | 1 | 0h30m | 1 | 0h00m | 0 | 0h40m | 1 |",
            updated,
        )
        self.assertIn(work_time.WEEKLY_MANUAL_NOTE, updated)
        self.assertIn("## 手动打卡明细（保留）", updated)

    def test_archive_keeps_same_start_notes_attached_to_their_categories(self):
        ledger = """# 工作时长台账（按项目路径分类）

## 项目目录
### 工作项目
- `/work`

### planB 项目
- `/planb`

## 工作项目时间段

| 日期 | 起 | 止 | 时长 | 项目 | 备注(产出/状态) |
|---|---|---|---|---|---|
| 2026-08-10 周一 | 09:55 | 10:05 | 10 分钟 | work | 工作备注 |

## planB项目时间段

| 日期 | 起 | 止 | 时长 | 项目 | 备注(产出/状态) |
|---|---|---|---|---|---|
| 2026-08-10 周一 | 09:55 | 10:05 | 10 分钟 | planb | PlanB备注 |

## 其他项目时间段

| 日期 | 起 | 止 | 时长 | 项目 | 备注(产出/状态) |
|---|---|---|---|---|---|

## 周汇总（按项目分类自动统计）

| 自然周（周一~周日） | 工作项目时长 | 工作段数 | planB项目时长 | planB段数 | 其他项目时长 | 其他段数 | 分类合计 | 活跃天数 | 更新时间 |
|---|---|---|---|---|---|---|---|---|---|

## 手动打卡明细

| 日期 | 起 | 止 | 时长 | 内容 | 备注 |
|---|---|---|---|---|---|
"""
        timezone = dt.timezone(dt.timedelta(hours=8))
        start = dt.datetime(2026, 8, 10, 9, 55, tzinfo=timezone)
        end = dt.datetime(2026, 8, 10, 10, 5, tzinfo=timezone)
        categorized = {category: [] for category in work_time.CATEGORIES}
        categorized["工作项目"].append(
            work_time.Segment("工作项目", start, end, 600, ("/work",))
        )
        categorized["planB项目"].append(
            work_time.Segment("planB项目", start, end, 600, ("/planb",))
        )
        config = {"工作项目": ["/work"], "planB项目": ["/planb"]}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.md"
            path.write_text(ledger, encoding="utf-8")
            work_time.archive_ledger(
                path,
                config,
                categorized,
                dt.datetime(2026, 8, 10, tzinfo=timezone),
                dt.datetime(2026, 8, 11, tzinfo=timezone),
            )
            updated = path.read_text(encoding="utf-8")

        work_section = work_time.section_text(updated, "工作项目时间段")
        planb_section = work_time.section_text(updated, "planB项目时间段")
        self.assertIn("| 工作备注 |", work_section)
        self.assertNotIn("PlanB备注", work_section)
        self.assertIn("| PlanB备注 |", planb_section)
        self.assertNotIn("工作备注", planb_section)

    def test_manual_clock_immediately_adds_completed_start_segments_to_planb(self):
        ledger = """# 工作时长台账

## 周汇总（按项目分类自动统计）

| 自然周（周一~周日） | 工作项目时长 | 工作段数 | planB项目时长 | planB段数 | 其他项目时长 | 其他段数 | 分类合计 | 活跃天数 | 更新时间 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-08-10 ~ 2026-08-16 | 1h01m | 1 | 2h02m | 4 | 3h03m | 3 | 6h06m | 1 | 2026-08-16 |

## 手动打卡明细（自动抓不到的工作：看书/开会/线下/纯思考；精确起止，不加补偿）

| 日期 | 起 | 止 | 时长 | 内容 | 备注 |
|---|---|---|---|---|---|
| 2026-08-10 周一 | 10:00 | 10:10 | 10 分钟 | 已有手动段 |  |
"""
        timezone = dt.timezone(dt.timedelta(hours=8))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.md"
            path.write_text(ledger, encoding="utf-8")
            original_ledger = manual_clock.LEDGER
            manual_clock.LEDGER = str(path)
            try:
                manual_clock._write_ledger(
                    dt.datetime(2026, 8, 10, 11, 0, tzinfo=timezone),
                    dt.datetime(2026, 8, 10, 11, 20, tzinfo=timezone),
                    "新手动段",
                )
                manual_clock._write_ledger(
                    dt.datetime(2026, 8, 10, 12, 0, tzinfo=timezone),
                    dt.datetime(2026, 8, 10, 12, 5, tzinfo=timezone),
                    "第二段",
                )
            finally:
                manual_clock.LEDGER = original_ledger
            updated = path.read_text(encoding="utf-8")
        self.assertIn(
            "| 2026-08-10 ~ 2026-08-16 | 1h01m | 1 | 2h37m | 7 | 3h03m | 3 | 6h41m | 1 |",
            updated,
        )
        self.assertEqual(updated.count("已有手动段"), 1)
        self.assertEqual(updated.count("新手动段"), 1)
        self.assertEqual(updated.count("第二段"), 1)
        self.assertIn(work_time.WEEKLY_MANUAL_NOTE, updated)

    def test_table_config_is_restored_to_project_directory_lists(self):
        ledger = """# 工作时长台账

## 项目分类（手动维护）

| 分类 | 项目路径 |
|---|---|
| 工作项目 | `/work` |
| planB项目 | `/planb` |
| 其他项目 | 未命中以上路径的全部项目（自动归类） |

## 手动打卡明细
"""
        config = work_time.parse_project_config(ledger)
        updated = work_time.normalize_config_section(ledger, config)
        self.assertIn("## 项目目录\n### 工作项目\n- `/work`", updated)
        self.assertIn("### planB 项目\n- `/planb`", updated)
        self.assertNotIn("## 项目分类", updated)
        self.assertNotIn("| 分类 | 项目路径 |", updated)

    def test_existing_project_directory_format_is_not_rewritten(self):
        ledger = """# 工作时长台账

## 项目目录
### 工作项目
- `/work`

<!-- 我的自定义分隔 -->
### planB 项目
- `/planb`

## 手动打卡明细
"""
        config = work_time.parse_project_config(ledger)
        self.assertEqual(work_time.normalize_config_section(ledger, config), ledger)


if __name__ == "__main__":
    unittest.main()
