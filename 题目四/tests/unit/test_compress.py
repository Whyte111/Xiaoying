"""确定性压缩工具的测试。

核心主张：**同样的草案 + 同样的目标，必然得到同样的结果**。
这是把“超时长修正”从概率性变成可复现的关键。
"""

import unittest

from src.domain.errors import ErrorCode
from src.domain.schemas import ScriptDraft, Shot
from src.tools.compress import compress
from src.tools.duration import count_speech_units, estimate

WPS = 5.0


def draft_of(*texts):
    shots = []
    for i, text in enumerate(texts, start=1):
        shots.append(
            Shot(
                shot_id="s{n}".format(n=i),
                narration=text,
                material_ids=["P01"],
                fact_refs=[{"material_id": "P01", "fact": "支持字幕"}],
            )
        )
    return ScriptDraft(title="标题", shots=shots)


LONG = [
    "开场：做短视频的人越来越多，真正卡住大家的往往不是创意，而是拍完之后整理素材到剪出成片这一整段流程，重复又耗时。",
    "中段：这款智能剪辑器支持字幕，把素材整理好以后可以直接进入成片环节，把重复的机械操作交给工具处理。",
    "收尾：如果你也在为成片流程发愁，可以先从基础功能试起，再决定要不要把整套工作流搬过来。",
]


class TestCompressDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        a = compress(draft_of(*LONG), 18.0, WPS)
        b = compress(draft_of(*LONG), 18.0, WPS)
        self.assertTrue(a.ok)
        self.assertEqual(a.data["per_shot"], b.data["per_shot"])
        self.assertEqual(a.digest, b.digest)

    def test_result_fits_target(self):
        target = 18.0
        d = draft_of(*LONG)
        r = compress(d, target, WPS)
        self.assertTrue(r.ok, r.digest)
        for item, shot in zip(r.data["per_shot"], d.shots):
            shot.narration = item["narration"]
        check = estimate(d, WPS, target)
        self.assertTrue(check.data["passed"], check.digest)
        self.assertLessEqual(check.data["total_s"], target + 1e-9)

    def test_idempotent(self):
        d = draft_of(*LONG)
        first = compress(d, 18.0, WPS)
        for item, shot in zip(first.data["per_shot"], d.shots):
            shot.narration = item["narration"]
        second = compress(d, 18.0, WPS)
        self.assertTrue(second.ok)
        self.assertEqual(first.data["per_shot"], second.data["per_shot"])
        self.assertEqual(first.data["after_s"], second.data["after_s"])

    def test_preserves_fact_traceability(self):
        d = draft_of(*LONG)
        r = compress(d, 18.0, WPS)
        self.assertTrue(r.ok)
        for shot in d.shots:
            self.assertTrue(shot.material_ids, "压缩不应清空素材绑定")
            self.assertTrue(shot.fact_refs, "压缩不应清空事实引用")
            self.assertEqual(shot.fact_refs[0]["fact"], "支持字幕")

    def test_never_empties_a_shot(self):
        d = draft_of(*LONG)
        r = compress(d, 20.0, WPS)
        self.assertTrue(r.ok, r.digest)
        for item in r.data["per_shot"]:
            self.assertGreaterEqual(item["units"], 1, "分镜被压成空旁白")


class TestCompressInfeasible(unittest.TestCase):
    def test_too_many_shots_for_target(self):
        """每镜至少 1 秒：4 个分镜塞不进 3 秒，必须明确报不可满足。"""
        d = draft_of("一二三四", "五六七八", "九十甲乙", "丙丁戊己")
        r = compress(d, 3.0, WPS)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, ErrorCode.UNSATISFIABLE.value)
        self.assertIn("分镜", r.digest)

    def test_empty_draft_is_tool_error(self):
        r = compress(ScriptDraft(title="空", shots=[]), 10.0, WPS)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, ErrorCode.TOOL_ERROR.value)

    def test_zero_wps_is_tool_error(self):
        r = compress(draft_of(*LONG), 10.0, 0)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, ErrorCode.TOOL_ERROR.value)


class TestCompressContentFloor(unittest.TestCase):
    """内容底线：为凑时长删掉一半以上内容属于把脚本压没，必须判不可满足。"""

    def test_rejects_when_cut_exceeds_half(self):
        d = draft_of(*LONG)
        r = compress(d, 6.0, WPS)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, ErrorCode.UNSATISFIABLE.value)
        self.assertIn("超过 50%", r.digest)

    def test_allows_moderate_cut(self):
        d = draft_of(*LONG)
        r = compress(d, 20.0, WPS)
        self.assertTrue(r.ok, r.digest)
        kept = sum(p["units"] for p in r.data["per_shot"])
        self.assertGreaterEqual(kept, 0.5 * 148)


class TestCompressQuality(unittest.TestCase):
    def test_trims_at_punctuation_when_possible(self):
        text = "第一句话在这里。第二句话在这里。第三句话在这里。"
        d = draft_of(text, text)
        r = compress(d, 6.0, WPS)
        self.assertTrue(r.ok, r.digest)
        for item in r.data["per_shot"]:
            self.assertTrue(item["narration"], "旁白不应为空")
            self.assertLessEqual(count_speech_units(item["narration"]), len(text))


if __name__ == "__main__":
    unittest.main()
