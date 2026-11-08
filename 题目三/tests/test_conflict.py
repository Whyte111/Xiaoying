"""冲突检测的单元测试：覆盖标题相同、标题被改写、单位族不同、数值相同四类。"""
from __future__ import annotations

import unittest

from src import conflict
from src.corpus import DocSegment


def seg(doc_id: str, title: str, text: str) -> DocSegment:
    return DocSegment(doc_id, "2024-01-01", f"{doc_id}#1", title, text, 0, len(text), "mem")


class TestConflictDetect(unittest.TestCase):
    def test_same_title_detected(self):
        a = seg("D01", "报销时限", "员工应在差旅结束后 30 个自然日内提交报销申请，逾期需说明原因。")
        b = seg("D02", "报销时限", "报销申请须在费用发生后 15 个工作日内提交，逾期原则上不予受理。")
        found = conflict.detect([a, b])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].docs(), ["D01", "D02"])

    def test_renamed_title_still_detected(self):
        """评审把小节名改掉后，仅靠标题匹配会漏报——这条就是那个回归。"""
        a = seg("D01", "报销时限", "员工应在差旅结束后 30 个自然日内提交报销申请，逾期需说明原因。")
        b = seg("D02", "提交要求", "报销申请须在费用发生后 15 个工作日内提交，逾期原则上不予受理。")
        self.assertEqual(len(conflict.detect([a, b])), 1)

    def test_different_unit_family_not_conflict(self):
        a = seg("D01", "住宿标准", "一线城市住宿标准为每人每晚 600 元，超出部分个人承担。")
        b = seg("D02", "年假申请", "年假需提前 3 个工作日在系统中提交申请，由主管审批。")
        self.assertEqual(conflict.detect([a, b]), [])

    def test_same_value_not_conflict(self):
        a = seg("D01", "报销时限", "员工应在费用发生后 30 个自然日内提交报销申请。")
        b = seg("D02", "报销时限", "报销申请须在费用发生后 30 个自然日内提交。")
        self.assertEqual(conflict.detect([a, b]), [])

    def test_same_doc_not_conflict(self):
        a = seg("D01", "报销时限", "员工应在 30 个自然日内提交报销申请。")
        b = seg("D01", "报销时限", "报销申请须在 15 个工作日内提交。")
        self.assertEqual(conflict.detect([a, b]), [])

    def test_synonym_unit_not_conflict(self):
        """「30 个自然日」与「30 天」是同一规定，不能误报冲突。"""
        a = seg("D01", "报销时限", "员工应在差旅结束后 30 个自然日内提交报销申请，逾期需说明原因。")
        b = seg("D02", "报销时限", "员工应在差旅结束后 30 天内提交报销申请，逾期需说明原因。")
        self.assertEqual(conflict.detect([a, b]), [])

    def test_workday_vs_day_still_conflict(self):
        """但 15 个工作日 != 30 天，仍然要报冲突。"""
        a = seg("D01", "报销时限", "员工应在差旅结束后 30 天内提交报销申请，逾期需说明原因。")
        b = seg("D02", "报销时限", "报销申请须在费用发生后 15 个工作日内提交，逾期不予受理。")
        self.assertEqual(len(conflict.detect([a, b])), 1)


if __name__ == "__main__":
    unittest.main()
