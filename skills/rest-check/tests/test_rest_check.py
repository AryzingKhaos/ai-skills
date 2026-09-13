#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rest-check 回归测试：python3 tests/test_rest_check.py

盯的是那些「会静默出错」的地方：时长格式、超限判定、日期并集、基线只降不升。
"""

import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import rest_check as rc  # noqa: E402


def week(mon):
    return "%s ~ %s" % (mon.isoformat(), (mon + dt.timedelta(days=6)).isoformat())


def write_work(path, weeks, work_days=(), planb_days=(), manual_days=(), other_days=()):
    def seg(days):
        return "\n".join("| %s 周一 | 10:00 | 11:00 | 1 小时 | p |  |" % d.isoformat()
                         for d in days)
    rows = "\n".join(
        "| %s | %s | 5 | 3h00m | 4 | 1h00m | 2 | 20h00m | 7 | 2026-09-04 |"
        % (week(m), rc.fmt_dur(w)) for m, w in weeks)
    with open(path, "w", encoding="utf-8") as f:
        f.write("""# 工作时长台账

## 工作项目时间段

| 日期 | 起 | 止 | 时长 | 项目 | 备注 |
|---|---|---|---|---|---|
%s

## planB项目时间段

| 日期 | 起 | 止 | 时长 | 项目 | 备注 |
|---|---|---|---|---|---|
%s

## 其他项目时间段

| 日期 | 起 | 止 | 时长 | 项目 | 备注 |
|---|---|---|---|---|---|
%s

## 周汇总（自动记录 + 手动打卡）

| 自然周 | 工作项目时长 | 工作段数 | planB项目时长 | planB段数 | 其他项目时长 | 其他段数 | 分类合计 | 活跃天数 | 更新时间 |
|---|---|---|---|---|---|---|---|---|---|
%s

## 手动打卡明细

| 日期 | 起 | 止 | 时长 | 内容 | 备注 |
|---|---|---|---|---|---|
%s
""" % (seg(work_days), seg(planb_days), seg(other_days), rows,
       "\n".join("| %s 周日 | 13:00 | 18:00 | 5 小时 | 看书 |  |" % d.isoformat()
                 for d in manual_days)))


def write_dota(path, rows):
    body = "\n".join(
        "| %s | %s | 20 | 10 胜 10 负 | 10h | %s | 1h00m | 2026-09-04 |  |" % (week(m), s, st)
        for m, s, st in rows)
    with open(path, "w", encoding="utf-8") as f:
        f.write("""# DOTA2 时长台账

| 自然周 | 真实消耗时长 | 局数 | 天梯战绩 | 本周时限 | 是否超限 | 当前结余量 | 更新时间 | 备注 |
|---|---|---|---|---|---|---|---|---|
%s
""" % body)


class TestParsing(unittest.TestCase):
    def test_both_duration_formats(self):
        """DOTA2时长.md 2026-08-17 起换了格式，两种都得吃下。"""
        self.assertEqual(rc.parse_dur("17h52m"), 17 * 60 + 52)
        self.assertEqual(rc.parse_dur("17 小时 52 分钟"), 17 * 60 + 52)
        self.assertEqual(rc.parse_dur("0h31m"), 31)
        self.assertEqual(rc.parse_dur("43 分钟"), 43)
        self.assertEqual(rc.parse_dur("1 小时 4 分钟"), 64)
        self.assertEqual(rc.parse_dur("2h"), 120)

    def test_non_durations(self):
        for s in ("", "—", "待结算", None):
            self.assertIsNone(rc.parse_dur(s))

    def test_over_limit_classification(self):
        self.assertIs(rc.classify_over("⚠️ 已超 36h49m"), True)
        self.assertIs(rc.classify_over("🛑 已超 6h00m"), True)
        self.assertIs(rc.classify_over("✅ 未超（剩 9h28m）"), False)
        # 花结余是已批准额度的提取，不算超限
        self.assertIs(rc.classify_over("💰 动用结余 1h59m（合法）"), False)
        self.assertIsNone(rc.classify_over("—"))


class TestRealFiles(unittest.TestCase):
    """真实台账必须解析得出东西——防止格式漂移后静默返回空。"""

    def test_real_sources_parse(self):
        if not (os.path.exists(rc.WORK_FILE) and os.path.exists(rc.DOTA_FILE)):
            self.skipTest("真实台账不在本机")
        weeks, active = rc.load_work(rc.WORK_FILE)
        self.assertGreater(len(weeks), 0, "周汇总表没解析出行")
        self.assertGreater(len(active), 0, "明细表没解析出日期")
        dota = rc.load_dota(rc.DOTA_FILE)
        self.assertGreater(len(dota), 0, "DOTA 台账没解析出行")
        # 新格式那几周必须在，且时长非空（旧正则会在这里静默丢行）
        for mon in (dt.date(2026, 8, 17), dt.date(2026, 8, 24)):
            self.assertIn(mon, dota)
            self.assertIsNotNone(dota[mon]["spent_min"], "%s 时长解析失败" % mon)

    def test_documented_baseline_reproduces(self):
        """机制文档第六节记录基线 17h43m，脚本必须算得出同一个数。"""
        if not os.path.exists(rc.WORK_FILE):
            self.skipTest("真实台账不在本机")
        weeks, _ = rc.load_work(rc.WORK_FILE)
        state = {"baseline_min": None, "baseline_weeks": 0, "baseline_date": None}
        rc.recompute_baseline(weeks, state, dt.date(2026, 9, 4))
        self.assertEqual(rc.fmt_dur(state["baseline_min"]), "17h43m")


class TestRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.today = dt.date(2026, 9, 4)
        self.mons = [dt.date(2026, 7, 6) + dt.timedelta(weeks=i) for i in range(8)]
        self.work = os.path.join(self.tmp, "work.md")
        self.dota = os.path.join(self.tmp, "dota.md")

    def judge(self, takes=()):
        weeks, active = rc.load_work(self.work)
        dota = rc.load_dota(self.dota)
        state = {"baseline_min": None, "baseline_weeks": 0, "baseline_date": None,
                 "takes": [{"date": d, "quarter": rc.quarter_label(d), "rule": "",
                            "recorded": "", "note": ""} for d in takes],
                 "log": []}
        rc.recompute_baseline(weeks, state, self.today)
        return rc.evaluate(self.today, weeks, active, dota, state)

    def hit(self, r):
        return sorted(n for n, _ in r["rules"])

    def test_rule1_two_overloaded_weeks(self):
        weeks = [(m, 1000) for m in self.mons[:6]] + [(self.mons[6], 1400), (self.mons[7], 1450)]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        self.assertIn(1, self.hit(self.judge(takes=[dt.date(2026, 7, 5)])))

    def test_rule1_needs_both_weeks(self):
        weeks = [(m, 1000) for m in self.mons[:7]] + [(self.mons[7], 1450)]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        self.assertNotIn(1, self.hit(self.judge(takes=[dt.date(2026, 7, 5)])))

    def test_rule2_streak_counts_manual_clockin(self):
        """手动打卡的日子不在 planB 表里也必须算工作日（真实台账里出现过）。"""
        weeks = [(m, 900) for m in self.mons]
        days = [self.today - dt.timedelta(days=i) for i in range(1, 22)]
        # 中间那天只有手动打卡，没有自动记录
        gap = days[10]
        write_work(self.work, weeks,
                   work_days=[d for d in days if d != gap], manual_days=[gap])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        r = self.judge(takes=[dt.date(2026, 7, 5)])
        self.assertEqual(r["streak"], 21)
        self.assertIn(2, self.hit(r))

    def test_rule2_other_projects_are_not_work(self):
        """已确认口径：只有『其他项目』的一天，算休息日，streak 断在这里。"""
        weeks = [(m, 900) for m in self.mons]
        days = [self.today - dt.timedelta(days=i) for i in range(1, 22)]
        gap = days[5]
        write_work(self.work, weeks,
                   work_days=[d for d in days if d != gap], other_days=[gap])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        r = self.judge(takes=[dt.date(2026, 7, 5)])
        self.assertEqual(r["streak"], 5)
        self.assertNotIn(2, self.hit(r))

    def test_rule3_needs_and_not_or(self):
        """DOTA 超限但工时正常 = 只是在玩，不该触发。"""
        weeks = [(m, 900) for m in self.mons]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons[:7]]
                   + [(self.mons[7], "24 小时 52 分钟", "⚠️ 已超 14h52m")])
        self.assertNotIn(3, self.hit(self.judge(takes=[dt.date(2026, 7, 5)])))

    def test_rule3_fires_on_both(self):
        weeks = [(m, 1000) for m in self.mons[:7]] + [(self.mons[7], 1400)]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons[:7]]
                   + [(self.mons[7], "24 小时 52 分钟", "⚠️ 已超 14h52m")])
        self.assertIn(3, self.hit(self.judge(takes=[dt.date(2026, 7, 5)])))

    def test_rule3_balance_spend_is_not_over(self):
        weeks = [(m, 1000) for m in self.mons[:7]] + [(self.mons[7], 1400)]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons[:7]]
                   + [(self.mons[7], "21h10m", "💰 动用结余 6h10m（合法）")])
        self.assertNotIn(3, self.hit(self.judge(takes=[dt.date(2026, 7, 5)])))

    def test_rule4_forced_draw_and_quota(self):
        weeks = [(m, 900) for m in self.mons]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        # 本季度一天没用 → 强制支取
        r = self.judge()
        self.assertIn(4, self.hit(r))
        self.assertEqual(r["verdict"], "yes")
        # 用过一天 → 不再触发
        self.assertNotIn(4, self.hit(self.judge(takes=[dt.date(2026, 7, 5)])))

    def test_cooldown_blocks_trigger(self):
        weeks = [(m, 1000) for m in self.mons[:6]] + [(self.mons[6], 1400), (self.mons[7], 1450)]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        r = self.judge(takes=[self.today - dt.timedelta(days=5)])
        self.assertIn(1, self.hit(r))
        self.assertEqual(r["verdict"], "cooldown")

    def test_quota_exhausted(self):
        weeks = [(m, 1000) for m in self.mons[:6]] + [(self.mons[6], 1400), (self.mons[7], 1450)]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        r = self.judge(takes=[dt.date(2026, 7, 5), dt.date(2026, 7, 20)])
        self.assertEqual(r["verdict"], "exhausted")

    def test_current_week_excluded(self):
        """当周未过完，不能进任何判定。"""
        weeks = [(m, 900) for m in self.mons] + [(dt.date(2026, 8, 31), 3000)]
        write_work(self.work, weeks, work_days=[dt.date(2026, 8, 30)])
        write_dota(self.dota, [(m, "5h00m", "✅ 未超（剩 5h）") for m in self.mons])
        r = self.judge(takes=[dt.date(2026, 7, 5)])
        labels = [x["label"] for x in r["recent_weeks"]]
        self.assertTrue(all("2026-08-31" not in l for l in labels))


class TestBaselineRatchet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work.md")
        self.mons = [dt.date(2026, 7, 6) + dt.timedelta(weeks=i) for i in range(8)]

    def test_only_goes_down(self):
        write_work(self.work, [(m, 1000) for m in self.mons])
        weeks, _ = rc.load_work(self.work)
        state = {"baseline_min": 900, "baseline_weeks": 8, "baseline_date": None}
        rc.recompute_baseline(weeks, state, dt.date(2026, 9, 4))
        self.assertEqual(state["baseline_min"], 900, "算出更高的值不该采纳")

        state = {"baseline_min": 1200, "baseline_weeks": 8, "baseline_date": None}
        rc.recompute_baseline(weeks, state, dt.date(2026, 9, 4))
        self.assertEqual(state["baseline_min"], 1000, "算出更低的值应当下调")

    def test_threshold_is_baseline_times_ratio(self):
        self.assertEqual(rc.threshold_of(17 * 60 + 43), 1329)   # 22h09m
        self.assertEqual(rc.fmt_dur(rc.threshold_of(17 * 60 + 43)), "22h09m")


class TestLedgerRoundTrip(unittest.TestCase):
    def test_save_then_load(self):
        path = os.path.join(tempfile.mkdtemp(), "台账.md")
        state = {"baseline_min": 1063, "baseline_weeks": 6,
                 "baseline_date": dt.date(2026, 9, 4),
                 "takes": [{"date": dt.date(2026, 9, 5), "quarter": "2026Q3",
                            "rule": "第4条", "recorded": "2026-09-04", "note": "强制支取"}],
                 "log": [["2026-09-04", "🛑 该请假", "第4条", "16h45m", "否", "5天", "0/2"]]}
        rc.save_ledger(path, state)
        back = rc.load_ledger(path)
        self.assertEqual(back["baseline_min"], 1063)
        self.assertEqual(back["baseline_weeks"], 6)
        self.assertEqual(len(back["takes"]), 1)
        self.assertEqual(back["takes"][0]["date"], dt.date(2026, 9, 5))
        self.assertEqual(back["takes"][0]["note"], "强制支取")
        self.assertEqual(len(back["log"]), 1)

    def test_empty_ledger_placeholders_not_parsed_as_data(self):
        path = os.path.join(tempfile.mkdtemp(), "台账.md")
        state = {"baseline_min": 1063, "baseline_weeks": 6,
                 "baseline_date": dt.date(2026, 9, 4), "takes": [], "log": []}
        rc.save_ledger(path, state)
        back = rc.load_ledger(path)
        self.assertEqual(back["takes"], [])
        self.assertEqual(back["log"], [])


class TestQuarter(unittest.TestCase):
    def test_bounds_and_labels(self):
        self.assertEqual(rc.quarter_label(dt.date(2026, 9, 4)), "2026Q3")
        self.assertEqual(rc.quarter_bounds(dt.date(2026, 9, 4)),
                         (dt.date(2026, 7, 1), dt.date(2026, 9, 30)))
        self.assertEqual(rc.quarter_bounds(dt.date(2026, 12, 31)),
                         (dt.date(2026, 10, 1), dt.date(2026, 12, 31)))
        pct, _, _ = rc.quarter_progress(dt.date(2026, 8, 15))
        self.assertGreater(pct, 0.49)
        self.assertLess(pct, 0.52)


if __name__ == "__main__":
    unittest.main(verbosity=2)
