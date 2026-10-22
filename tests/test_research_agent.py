"""Smoke tests for the offline IdeaScout reference pipeline."""

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "research_agent.py"

spec = importlib.util.spec_from_file_location("research_agent", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

DEMO_TOPIC = "小米 SU7 vs Model 3 对比分析"


def run_cli(args, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        **kwargs,
    )


class FormulaTests(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertEqual(sum(mod.SCORE_WEIGHTS.values()), 1.0)

    def test_score_matches_rounded_weighted_terms(self):
        facts = [
            {"source_type": "official", "source_count": 2, "age_days": 0, "contradicted": False},
            {"source_type": "official", "source_count": 2, "age_days": 0, "contradicted": False},
            {"source_type": "unknown", "source_count": 1, "age_days": 200, "contradicted": True},
        ]
        score, parts = mod.score_confidence(facts)
        self.assertEqual(parts["source_trust"], round((100 + 100 + 20) / 3, 1))
        self.assertEqual(parts["timeliness"], round((100 + 100 + 0) / 3, 1))
        self.assertEqual(parts["cross_validation"], round(2 / 3 * 100, 1))
        self.assertEqual(parts["consistency"], round(2 / 3 * 100, 1))
        weighted = (
            parts["source_trust"] * 0.3
            + parts["timeliness"] * 0.2
            + parts["cross_validation"] * 0.3
            + parts["consistency"] * 0.2
        )
        self.assertEqual(score, round(weighted, 1))

    def test_empty_facts_score_zero(self):
        score, parts = mod.score_confidence([])
        self.assertEqual(score, 0.0)
        self.assertEqual(set(parts.values()), {0.0})

    def test_unknown_source_type_uses_low_trust(self):
        _score, parts = mod.score_confidence(
            [{"source_type": "podcast", "source_count": 1, "age_days": 0, "contradicted": False}]
        )
        self.assertEqual(parts["source_trust"], 20.0)

    def test_missing_age_is_stale(self):
        _score, parts = mod.score_confidence([{"source_type": "official"}])
        self.assertEqual(parts["timeliness"], round(mod.freshness(180), 1))
        self.assertTrue(mod.gate_failures(100, [{"source_type": "official"}], [], []))

    def test_freshness_is_clamped(self):
        self.assertEqual(mod.freshness(-10), 100.0)
        self.assertEqual(mod.freshness(0), 100.0)
        self.assertEqual(mod.freshness(200), 0.0)
        self.assertEqual(mod.freshness(400), 0.0)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.fresh = [{"age_days": 1, "source_count": 2, "contradicted": False}]

    def test_eighty_passes_and_just_below_fails(self):
        self.assertEqual(mod.gate_failures(80.0, self.fresh, [], []), [])
        self.assertTrue(mod.gate_failures(79.9, self.fresh, [], []))

    def test_two_gaps_keep_the_loop_open(self):
        self.assertTrue(mod.gate_failures(95, self.fresh, ["a", "b"], []))
        self.assertEqual(mod.gate_failures(95, self.fresh, ["only one"], []), [])

    def test_open_contradiction_keeps_the_loop_open(self):
        self.assertTrue(mod.gate_failures(95, self.fresh, [], [{"resolved": False}]))
        self.assertEqual(
            mod.gate_failures(95, self.fresh, [], [{"resolved": True}]),
            [],
        )

    def test_stale_boundary_is_over_90_days(self):
        self.assertEqual(mod.gate_failures(95, [{"age_days": 90}], [], []), [])
        self.assertTrue(mod.gate_failures(95, [{"age_days": 91}], [], []))


class PlannerTests(unittest.TestCase):
    def test_chinese_comparison_uses_the_topic(self):
        plan = mod.plan_subquestions(DEMO_TOPIC)
        self.assertEqual(len(plan), 5)
        self.assertIn("价格与获取成本", plan[0])
        self.assertTrue(all(item.startswith(DEMO_TOPIC) for item in plan))
        self.assertNotIn("智能驾驶", " ".join(plan))

    def test_chinese_generic_topic_is_not_a_car_plan(self):
        plan = mod.plan_subquestions("量子计算的近期进展")
        self.assertIn("背景与定义", plan[0])
        self.assertNotIn("价格与获取成本", plan[0])
        self.assertNotIn("智能驾驶", " ".join(plan))

    def test_english_comparison_and_whitespace(self):
        plan = mod.plan_subquestions("  rust vs go  ")
        self.assertTrue(plan[0].startswith("rust vs go · Price and access"))

    def test_empty_topic_is_rejected(self):
        with self.assertRaises(ValueError):
            mod.plan_subquestions("   ")


class PipelineTests(unittest.TestCase):
    def test_sources_accumulate_and_are_all_extracted(self):
        plan = mod.plan_subquestions("A vs B")
        lengths = [len(mod.collect_sources(plan, n)) for n in (1, 2, 3)]
        self.assertEqual(lengths, [6, 9, 11])
        first = mod.collect_sources(plan, 1)
        second = mod.collect_sources(plan, 2)
        self.assertFalse(any(item["type"] == "official" for item in first))
        self.assertTrue(any(item["type"] == "official" for item in second))
        cards = mod.extract_cards(first)
        self.assertEqual(len(cards), len(first))
        _facts, contradictions = mod.validate_cards(cards)
        self.assertEqual(len(contradictions), 1)
        self.assertFalse(contradictions[0]["resolved"])
        _facts, later = mod.validate_cards(mod.extract_cards(second))
        self.assertTrue(later[0]["resolved"])

    def test_demo_topic_climbs_and_stops_on_the_gate(self):
        buf = io.StringIO()
        result = mod.run_pipeline(DEMO_TOPIC, out=buf)
        text = buf.getvalue()
        self.assertEqual([item["score"] for item in result["rounds"]], [56.0, 83.1, 99.0])
        self.assertEqual([item["source_count"] for item in result["rounds"]], [6, 9, 11])
        self.assertEqual(
            [item["card_count"] for item in result["rounds"]],
            [item["source_count"] for item in result["rounds"]],
        )
        self.assertEqual([len(item["gaps"]) for item in result["rounds"]], [4, 2, 0])
        self.assertEqual(result["stopped_reason"], "quality_gate")
        self.assertEqual(result["score"], 99.0)
        self.assertTrue(any(not item["resolved"] for item in result["rounds"][0]["contradictions"]))
        self.assertTrue(all(item["resolved"] for item in result["contradictions"]))
        self.assertNotIn("第 4 轮", text)
        self.assertNotIn("保值率", text)
        self.assertNotIn("入门门槛更低", text)
        self.assertNotIn("智能驾驶", text)
        self.assertIn("不是对「%s」的真实检索" % DEMO_TOPIC, result["report"])
        self.assertIn(
            "计算: 100.0×0.3 + 94.8×0.2 + 100.0×0.3 + 100.0×0.2 = 99.0",
            result["report"],
        )
        # The old evaluator printed 80 + gap_count * 5 and called it completeness.
        gaps = len(result["rounds"][0]["gaps"])
        self.assertNotIn("数据完整性: %s%%" % (80 + gaps * 5), text)

    def test_max_rounds_does_not_announce_a_round_it_will_not_run(self):
        buf = io.StringIO()
        result = mod.run_pipeline(DEMO_TOPIC, max_rounds=1, out=buf)
        text = buf.getvalue()
        self.assertEqual(result["stopped_reason"], "max_rounds")
        self.assertEqual(len(result["rounds"]), 1)
        self.assertNotIn("第 2 轮", text)
        self.assertIn("已达最大迭代次数 (1 轮)", text)

    def test_extra_round_budget_stops_when_the_gate_opens(self):
        buf = io.StringIO()
        result = mod.run_pipeline(DEMO_TOPIC, max_rounds=5, out=buf)
        self.assertEqual(len(result["rounds"]), 3)
        self.assertEqual(result["stopped_reason"], "quality_gate")
        self.assertNotIn("第 4 轮", buf.getvalue())

    def test_max_rounds_must_be_positive(self):
        with self.assertRaises(ValueError):
            mod.run_pipeline("topic", max_rounds=0, out=io.StringIO())

    def test_script_does_not_import_a_network_stack(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for banned in ("requests", "urllib", "http.client", "socket"):
            self.assertNotIn("import %s" % banned, text)


class CliTests(unittest.TestCase):
    def test_saved_demo_matches_the_documented_command(self):
        proc = run_cli([DEMO_TOPIC])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        expected = (ROOT / "demo-output.txt").read_text(encoding="utf-8")
        self.assertEqual(proc.stdout, expected)

    def test_help_and_version(self):
        help_proc = run_cli(["--help"])
        self.assertEqual(help_proc.returncode, 0)
        self.assertIn("--interactive", help_proc.stdout)
        self.assertIn("no network", help_proc.stdout)
        version = run_cli(["--version"])
        self.assertEqual(version.returncode, 0)
        self.assertIn(mod.__version__, version.stdout)

    def test_missing_topic_and_bad_round_cap(self):
        missing = run_cli([])
        self.assertEqual(missing.returncode, 2)
        self.assertIn("missing topic", missing.stderr)
        bad = run_cli(["--max-rounds", "0", "topic"])
        self.assertEqual(bad.returncode, 2)
        self.assertIn("--max-rounds must be >= 1", bad.stderr)

    def test_interactive_reads_stdin_and_is_not_the_topic(self):
        proc = run_cli(["--interactive"], input="rust vs go\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rust vs go", proc.stdout)
        self.assertIn("Price and access", proc.stdout)
        self.assertNotIn("话题: --interactive", proc.stdout)

    def test_json_is_stdout_and_the_log_is_stderr(self):
        proc = run_cli(["--json", "--max-rounds", "1", DEMO_TOPIC])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["topic"], DEMO_TOPIC)
        self.assertEqual(payload["score"], 56.0)
        self.assertEqual(payload["stopped_reason"], "max_rounds")
        self.assertEqual(payload["version"], mod.__version__)
        self.assertIn("本地演示", proc.stderr)
        self.assertNotIn("🎯", proc.stdout)


if __name__ == "__main__":
    unittest.main()
