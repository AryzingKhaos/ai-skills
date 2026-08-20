import datetime
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "playtime.py"
SPEC = importlib.util.spec_from_file_location("dota2_playtime", MODULE_PATH)
playtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(playtime)


class BudgetRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dota_ledger = self.root / "DOTA2时长.md"
        self.work_ledger = self.root / "工作时长.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_work_ledger(self, rows):
        self.work_ledger.write_text(
            "# 工作时长台账\n\n"
            "## planB项目时间段\n\n"
            "| 日期 | planB项目时长 |\n"
            "|---|---|\n"
            "| 2026-08-17 | 99h00m |\n\n"
            "## 周汇总（按项目分类自动统计）\n\n"
            "| 自然周（周一~周日） | 工作项目时长 | planB项目时长 | 更新时间 |\n"
            "|---|---|---|---|\n"
            + "".join(rows),
            encoding="utf-8",
        )

    def test_reads_planb_from_matching_weekly_summary_row(self):
        self.write_work_ledger([
            "| 2026-08-10 ~ 2026-08-16 | 18h23m | 4h18m | 2026-08-16 |\n",
            "| 2026-08-17 ~ 2026-08-23 | 2h00m | 5h45m | 2026-08-20 |\n",
        ])

        seconds = playtime.read_weekly_planb_seconds(
            self.work_ledger, datetime.date(2026, 8, 17)
        )

        self.assertEqual(seconds, 5 * 3600 + 45 * 60)

    def test_week_before_start_keeps_legacy_15h_and_ignores_planb(self):
        self.write_work_ledger([
            "| 2026-08-10 ~ 2026-08-16 | 18h23m | 4h18m | 2026-08-16 |\n"
        ])

        result = playtime.resolve_week_budget(
            datetime.date(2026, 8, 10), True, self.dota_ledger, self.work_ledger
        )

        self.assertFalse(result["rule_active"])
        self.assertEqual(result["base_cap_hours"], 15.0)
        self.assertEqual(result["planb_bonus_seconds"], 0)
        self.assertEqual(result["effective_cap_hours"], 15.0)

    def test_start_week_uses_10h_base_plus_planb(self):
        self.write_work_ledger([
            "| 2026-08-17 ~ 2026-08-23 | 2h00m | 4h18m | 2026-08-20 |\n"
        ])

        result = playtime.resolve_week_budget(
            datetime.date(2026, 8, 17), True, self.dota_ledger, self.work_ledger
        )

        self.assertTrue(result["rule_active"])
        self.assertEqual(result["base_cap_hours"], 10.0)
        self.assertEqual(result["planb_bonus_seconds"], 4 * 3600 + 18 * 60)
        self.assertAlmostEqual(result["effective_cap_hours"], 14.3)
        self.assertEqual(result["planb_bonus_source"], "work_ledger")

    def test_manual_ledger_base_is_preserved_before_adding_planb(self):
        self.dota_ledger.write_text(
            "| 自然周（周一~周日） | 真实消耗时长 | 局数 | 天梯战绩 | 本周时限 |\n"
            "|---|---|---|---|---|\n"
            "| 2026-08-17 ~ 2026-08-23 | 0h00m | 0 | 0 胜 0 负 | 12h |\n",
            encoding="utf-8",
        )
        self.write_work_ledger([
            "| 2026-08-17 ~ 2026-08-23 | 2h00m | 4h18m | 2026-08-20 |\n"
        ])

        result = playtime.resolve_week_budget(
            datetime.date(2026, 8, 17), True, self.dota_ledger, self.work_ledger
        )

        self.assertEqual(result["base_cap_source"], "ledger")
        self.assertEqual(result["base_cap_hours"], 12.0)
        self.assertAlmostEqual(result["effective_cap_hours"], 16.3)

    def test_missing_weekly_summary_uses_zero_bonus_and_marks_missing(self):
        self.write_work_ledger([])

        result = playtime.resolve_week_budget(
            datetime.date(2026, 8, 17), True, self.dota_ledger, self.work_ledger
        )

        self.assertEqual(result["base_cap_hours"], 10.0)
        self.assertEqual(result["planb_bonus_seconds"], 0)
        self.assertEqual(result["planb_bonus_source"], "missing")
        self.assertEqual(result["effective_cap_hours"], 10.0)

    def test_days_mode_does_not_apply_weekly_planb_rule(self):
        self.write_work_ledger([
            "| 2026-08-17 ~ 2026-08-23 | 2h00m | 4h18m | 2026-08-20 |\n"
        ])

        result = playtime.resolve_week_budget(
            datetime.date(2026, 8, 17), False, self.dota_ledger, self.work_ledger
        )

        self.assertFalse(result["rule_active"])
        self.assertEqual(result["effective_cap_hours"], 15.0)

    def test_balance_is_calculated_after_planb_is_added(self):
        opening_balance = 2 * 3600 + 32 * 60
        dota_spend = 13 * 3600 + 55 * 60

        result = playtime.calculate_end_balance(opening_balance, 14.3, dota_spend)

        self.assertEqual(result, 2 * 3600 + 55 * 60)

    def test_main_reports_planb_breakdown_and_effective_cap(self):
        self.write_work_ledger([
            "| 2026-08-17 ~ 2026-08-23 | 2h00m | 4h18m | 2026-08-20 |\n"
        ])

        class FixedDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                fixed = cls(
                    2026, 8, 20, 12, 0,
                    tzinfo=datetime.timezone(datetime.timedelta(hours=8)),
                )
                return fixed if tz is None else fixed.astimezone(tz)

        def fake_get(url, timeout=30):
            return [] if "/matches?" in url else {"rank_tier": 54}

        stdout = io.StringIO()
        argv = [
            "playtime.py",
            "--ledger", str(self.dota_ledger),
            "--work-ledger", str(self.work_ledger),
        ]
        with patch.object(playtime.datetime, "datetime", FixedDateTime), \
             patch.object(playtime, "get", fake_get), \
             patch.object(sys, "argv", argv), \
             redirect_stdout(stdout):
            playtime.main()

        output = stdout.getvalue()
        self.assertIn("基础时限 **10 小时**", output)
        self.assertIn("当周 Plan B **4 小时 18 分钟**", output)
        self.assertIn("当周有效时限：14 小时 18 分钟", output)
        signals_text = output.split("```json\n", 1)[1].split("\n```", 1)[0]
        budget = json.loads(signals_text)["budget"]
        self.assertEqual(budget["base_cap_hours"], 10.0)
        self.assertEqual(budget["planb_bonus_seconds"], 4 * 3600 + 18 * 60)
        self.assertEqual(budget["effective_cap_hours"], 14.3)


if __name__ == "__main__":
    unittest.main()
