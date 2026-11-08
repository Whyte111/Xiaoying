import unittest

from src.config import load_ctx
from src.domain.errors import ErrorCode
from src.domain.schemas import ScriptDraft, Shot
from src.tools import duration, rules, search


def draft_of(*shots):
    return ScriptDraft(title="测试标题", shots=list(shots))


class TestDuration(unittest.TestCase):
    def setUp(self):
        self.ctx = load_ctx()

    def test_units_count_ignores_space(self):
        # 支持(2) + 字幕(2) + 真好(2) = 6，空格不计
        self.assertEqual(duration.count_speech_units("支持字幕 真好"), 6)

    def test_deterministic(self):
        d = draft_of(Shot(shot_id="s1", narration="这是一句旁白"))
        a = duration.estimate(d, 4.5, 30)
        b = duration.estimate(d, 4.5, 30)
        self.assertEqual(a.data["total_s"], b.data["total_s"])
        self.assertEqual(a.digest, b.digest)

    def test_exceeded_emits_error_code(self):
        d = draft_of(Shot(shot_id="s1", narration="字" * 300))
        r = duration.estimate(d, 4.5, 30)
        self.assertFalse(r.data["passed"])
        self.assertEqual(r.error_code, ErrorCode.DURATION_EXCEEDED.value)

    def test_min_one_second_per_shot(self):
        d = draft_of(Shot(shot_id="s1", narration="短"), Shot(shot_id="s2", narration="短"))
        r = duration.estimate(d, 4.5, 30)
        self.assertEqual(r.data["total_s"], 2.0)

    def test_estimate_rejects_empty_draft(self):
        r = duration.estimate(draft_of(), 4.5, 30)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, ErrorCode.TOOL_ERROR.value)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.ctx = load_ctx()

    @staticmethod
    def ref(mid, fact):
        return {"material_id": mid, "fact": fact}

    def test_forbidden_phrase_detected(self):
        d = draft_of(
            Shot(
                shot_id="s1",
                narration="效率提升 80%",
                material_ids=["P01"],
                fact_refs=[self.ref("P01", "支持字幕")],
            )
        )
        r = rules.check(d, self.ctx.rules, self.ctx)
        self.assertFalse(r.data["passed"])
        codes = [v["code"] for v in r.data["violations"]]
        self.assertIn("FORBIDDEN_PHRASE", codes)

    def test_unsourced_shot_detected(self):
        d = draft_of(Shot(shot_id="s1", narration="支持字幕", material_ids=[]))
        r = rules.check(d, self.ctx.rules, self.ctx)
        codes = [v["code"] for v in r.data["violations"]]
        self.assertIn("UNSOURCED_CLAIM", codes)

    def test_missing_fact_ref_detected(self):
        d = draft_of(Shot(shot_id="s1", narration="支持字幕", material_ids=["P01"]))
        r = rules.check(d, self.ctx.rules, self.ctx)
        codes = [v["code"] for v in r.data["violations"]]
        self.assertIn("NO_FACT_REF", codes)

    def test_fabricated_fact_rejected(self):
        """引用素材里不存在的事实，必须判定为编造。"""
        d = draft_of(
            Shot(
                shot_id="s1",
                narration="自动帮你完成粗剪",
                material_ids=["P01"],
                fact_refs=[self.ref("P01", "自动粗剪")],
            )
        )
        r = rules.check(d, self.ctx.rules, self.ctx)
        codes = [v["code"] for v in r.data["violations"]]
        self.assertIn("INVALID_FACT_REF", codes)

    def test_fact_ref_outside_shot_materials(self):
        d = draft_of(
            Shot(
                shot_id="s1",
                narration="支持字幕",
                material_ids=["P01"],
                fact_refs=[self.ref("P02", "续航 12 小时")],
            )
        )
        r = rules.check(d, self.ctx.rules, self.ctx)
        codes = [v["code"] for v in r.data["violations"]]
        self.assertIn("FACT_REF_NOT_IN_SHOT", codes)

    def test_clean_draft_passes(self):
        d = draft_of(
            Shot(
                shot_id="s1",
                narration="这款工具支持字幕",
                material_ids=["P01"],
                fact_refs=[self.ref("P01", "支持字幕")],
            )
        )
        r = rules.check(d, self.ctx.rules, self.ctx)
        self.assertTrue(r.data["passed"], r.data["violations"])
        self.assertIsNone(r.error_code)

    def test_rules_do_not_check_duration(self):
        """冲突 2 的裁定：规则工具不产 DURATION_EXCEEDED。"""
        d = draft_of(
            Shot(
                shot_id="s1",
                narration="字" * 300,
                material_ids=["P01"],
                fact_refs=[self.ref("P01", "支持字幕")],
            )
        )
        r = rules.check(d, self.ctx.rules, self.ctx)
        for v in r.data["violations"]:
            self.assertNotEqual(v["code"], ErrorCode.DURATION_EXCEEDED.value)


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.ctx = load_ctx()

    def test_hit_by_id(self):
        r = search.search("", self.ctx, ids=["P01"])
        self.assertTrue(r.ok)
        self.assertEqual(r.data["hits"][0]["id"], "P01")

    def test_empty_returns_search_empty(self):
        r = search.search("不存在的关键词zzz", self.ctx)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, ErrorCode.SEARCH_EMPTY.value)

    def test_unknown_id_is_tool_error(self):
        r = search.search("", self.ctx, ids=["P99"])
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, ErrorCode.TOOL_ERROR.value)


if __name__ == "__main__":
    unittest.main()
