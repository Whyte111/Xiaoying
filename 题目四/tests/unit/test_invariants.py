"""不变量测试：把题目四的审核要点写成可执行的断言。

这些断言不看“结果看起来对不对”，只看**结构是否自洽**：
总时长能否由分镜复算、素材引用是否闭合、trace 是否完整。
任何一条挂了，交付物的可信度就不成立。
"""

import json
import unittest
from pathlib import Path

from src.agent.loop import Loop
from src.agent.policy import Policy
from src.config import ROOT, load_ctx
from src.domain.schemas import TaskInput
from src.llm.mock_client import MockModel
from src.observability.trace import TraceWriter

RUNS_DIR = ROOT / "examples" / "runs"
REQUIRED_SCRIPT_FIELDS = (
    "title",
    "shots",
    "estimated_total_s",
    "material_refs",
    "risks",
    "status",
)
REQUIRED_SHOT_FIELDS = ("shot_id", "narration", "duration_s", "material_ids", "fact_refs")


def count_units(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


class TestScriptInvariants(unittest.TestCase):
    def setUp(self):
        self.ctx = load_ctx()

    def assert_script_contract(self, script, target_duration_s):
        # 1. 交付字段齐全（题目四：标题、分镜、旁白/字幕、总时长、素材来源 ID、风险、状态）
        for field in REQUIRED_SCRIPT_FIELDS:
            self.assertIn(field, script, "缺少交付字段：{f}".format(f=field))
        self.assertTrue(script["shots"], "脚本没有任何分镜")

        # 2. 时长可由旁白复算，且与总时长一致（审核要点：分镜时长逐一对应）
        wps = float(
            self.ctx.platform_cfg(script.get("platform") or "douyin").get("words_per_second", 4.5)
        )
        recomputed_total = 0.0
        for shot in script["shots"]:
            for field in REQUIRED_SHOT_FIELDS:
                self.assertIn(field, shot, "分镜缺少字段：{f}".format(f=field))
            self.assertIsNotNone(shot["duration_s"], "分镜 {s} 没有时长".format(s=shot["shot_id"]))
            expected = max(1.0, round(count_units(shot["narration"]) / wps, 1))
            self.assertAlmostEqual(
                shot["duration_s"],
                expected,
                places=1,
                msg="分镜 {s} 时长 {a} 与旁白字数复算值 {b} 不一致".format(
                    s=shot["shot_id"], a=shot["duration_s"], b=expected
                ),
            )
            recomputed_total += shot["duration_s"]

        self.assertAlmostEqual(
            recomputed_total,
            script["estimated_total_s"],
            places=1,
            msg="总时长 {a} 与分镜之和 {b} 不一致".format(
                a=script["estimated_total_s"], b=recomputed_total
            ),
        )

        # 3. 超时长不得交付
        self.assertLessEqual(
            script["estimated_total_s"],
            target_duration_s + 1e-6,
            "交付脚本超出目标时长",
        )

        # 4. 素材引用闭合：declared refs == 分镜实际用到的素材
        union = sorted({m for s in script["shots"] for m in s["material_ids"]})
        self.assertEqual(union, sorted(script["material_refs"]), "素材引用清单与分镜不一致")

        # 5. 事实可溯源
        for shot in script["shots"]:
            self.assertTrue(shot["material_ids"], "分镜未绑定素材")
            self.assertTrue(shot["fact_refs"], "分镜未引用原文事实")
            for ref in shot["fact_refs"]:
                material = self.ctx.materials.get(ref["material_id"])
                self.assertIsNotNone(material, "引用了不存在的素材")
                self.assertIn(ref["fact"], material["facts"], "引用的事实不在素材原文中")

        # 6. 不得出现伪造效果数据
        full_text = " ".join([script["title"]] + [s["narration"] for s in script["shots"]])
        for phrase in self.ctx.rules.get("forbidden_phrases", []):
            if phrase:
                self.assertNotIn(phrase, full_text, "出现禁用表述：{p}".format(p=phrase))

    def test_mock_run_satisfies_contract(self):
        trace = TraceWriter(None)
        task = TaskInput.from_dict(
            {
                "topic": "智能剪辑器",
                "audience": "个人创作者",
                "platform": "douyin",
                "target_duration_s": 30,
                "style": "简洁口播",
            }
        )
        result = Loop(model=MockModel(), ctx=load_ctx(), policy=Policy(), trace=trace).run(task)
        self.assertEqual(result.status, "completed")
        self.assert_script_contract(result.script, 30)


class TestExistingArtifacts(unittest.TestCase):
    """拿历史产物反证：如果连自己产出的样例都不满足不变量，就不能交。"""

    def setUp(self):
        self.ctx = load_ctx()
        self.results = sorted(RUNS_DIR.glob("*.result.json")) if RUNS_DIR.exists() else []
        self.traces = sorted(RUNS_DIR.glob("*.trace.jsonl")) if RUNS_DIR.exists() else []

    def test_completed_artifacts_satisfy_contract(self):
        checked = 0
        checker = TestScriptInvariants()
        checker.setUp()
        for path in self.results:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") != "completed":
                continue
            script = data.get("script") or {}
            with self.subTest(artifact=path.name):
                checker.assert_script_contract(
                    script, float(script.get("target_duration_s") or 30)
                )
            checked += 1
        self.assertGreater(checked, 0, "没有任何 completed 产物可供校验")

    def test_traces_are_structurally_complete(self):
        self.assertGreater(len(self.traces), 0, "没有 trace 产物")
        for path in self.traces:
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            with self.subTest(trace=path.name):
                self.assertTrue(events, "空 trace")
                self.assertEqual(events[0].get("event"), "run_start", "trace 缺少 run_start")
                terminals = [e for e in events if e.get("event") == "terminal"]
                self.assertEqual(len(terminals), 1, "trace 必须且只能有一个 terminal")
                decisions = [e for e in events if e.get("event") == "decision"]
                for ev in decisions:
                    self.assertTrue(ev.get("decision_basis"), "决策缺少 decision_basis")
                    self.assertIn("action", ev)
                    self.assertIn("accepted", ev)
                # 每个被接受的决策都应有对应的 tool_result（finish/ask_clarify 除外）
                results = [e for e in events if e.get("event") == "tool_result"]
                accepted_actions = [
                    e["action"] for e in decisions if e.get("accepted")
                ]
                terminal_actions = [
                    a for a in accepted_actions if a in ("finish", "ask_clarify")
                ]
                self.assertGreaterEqual(
                    len(results),
                    len(accepted_actions) - len(terminal_actions),
                    "存在被接受的动作却没有工具结果",
                )


if __name__ == "__main__":
    unittest.main()
