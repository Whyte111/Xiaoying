"""最小闭环的验收测试：六项必测 + 两条边界。

运行：python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.agent.loop import Limits, run
from src.agent.policy import decide
from src.domain.gating import allowed_actions
from src.domain.models import Action, Facts, Material, Severity, Slot, Draft
from src.domain.slots import find_conflicts, overlaps, suggest_alternatives
from src.domain.rules import check_rules
from src.store.json_store import JsonStore
from src.tools.toolkit import Toolbox

ROOT = Path(__file__).resolve().parent.parent
DATA = str(ROOT / "data")


class DomainTest(unittest.TestCase):
    def test_touching_slots_do_not_conflict(self):
        a = Slot.parse("2026-11-10T18:00")
        b = Slot.parse("2026-11-10T19:00")
        self.assertFalse(overlaps(a, b))
        self.assertFalse(overlaps(b, a))

    def test_partial_overlap_conflicts(self):
        a = Slot.parse("2026-11-10T18:00")
        b = Slot.parse("2026-11-10T18:30")
        self.assertTrue(overlaps(a, b))

    def test_suggest_skips_booked_slot(self):
        booked = [Slot.parse("2026-11-10T18:00")]
        from src.domain.models import ExistingSlot

        existing = [ExistingSlot("S1", booked[0])]
        alts = suggest_alternatives(Slot.parse("2026-11-10T18:00"), existing)
        self.assertEqual(alts[0].label(), "2026-11-10 19:00-20:00")

    def test_block_rule_wins(self):
        m = Material(id="X", title="内部通知", requested_slot=Slot.parse("2026-11-10T10:00"))
        rules = [
            # 与 rules.json 中 R004 等价
            __import__("src.domain.models", fromlist=["Rule"]).Rule(
                "R004", Severity.BLOCK, "内部通知不得对外发布",
                {"field": "title", "op": "contains_any", "value": ["内部"]},
            )
        ]
        findings = check_rules(m, rules)
        self.assertEqual([f.rule_id for f in findings], ["R004"])

    def test_gating_blocks_illegal_action(self):
        facts = Facts(
            material=Material(id="X", title="t", requested_slot=Slot.parse("2026-11-10T10:00")),
            material_complete=True,
        )
        self.assertEqual(allowed_actions(facts), {Action.CHECK, Action.END})
        self.assertNotIn(Action.SAVE, allowed_actions(facts))

    def test_gating_stops_after_save_failure(self):
        facts = Facts(material=None)
        facts.save_failed = True
        self.assertEqual(allowed_actions(facts), {Action.END})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "drafts.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _draft(self, slot="2026-11-10T19:00"):
        return Draft("M01", __import__("src.domain.models", fromlist=["AuditStatus"]).AuditStatus.PASS, Slot.parse(slot))

    def test_same_signature_is_idempotent(self):
        store = JsonStore(self.path)
        first = store.upsert_draft(self._draft())
        second = store.upsert_draft(self._draft())
        self.assertEqual(first.kind, "created")
        self.assertEqual(second.kind, "idempotent")
        self.assertEqual(store.draft_count(), 1)
        self.assertEqual(JsonStore(self.path).draft_count(), 1)  # 重新加载仍是一份

    def test_changed_slot_updates_and_bumps_revision(self):
        store = JsonStore(self.path)
        store.upsert_draft(self._draft("2026-11-10T19:00"))
        outcome = store.upsert_draft(self._draft("2026-11-10T20:00"))
        self.assertEqual(outcome.kind, "updated")
        self.assertEqual(outcome.draft.revision, 2)
        self.assertEqual(store.draft_count(), 1)

    def test_injected_failure_does_not_write(self):
        store = JsonStore(self.path)
        store.fail_next = True
        outcome = store.upsert_draft(self._draft())
        self.assertFalse(outcome.ok)
        self.assertFalse(os.path.exists(self.path))


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.tmp.name, "drafts.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, material_id, fail_save=False, max_steps=8):
        toolbox = Toolbox.load(DATA, self.store_path)
        toolbox.store.fail_next = fail_save
        return run(material_id, toolbox, decide, Limits(max_steps=max_steps, max_tool_calls=max_steps))

    def test_normal_path_with_conflict_saves_draft_once(self):
        result = self._run("M01")
        self.assertEqual(result.status.value, "NEEDS_CONFIRM")
        self.assertEqual(result.item.audit_status.value, "PASS")
        self.assertEqual(result.item.suggested_slot.label(), "2026-11-10 19:00-20:00")
        self.assertEqual(result.item.draft_status.value, "SAVED_CREATED")
        self.assertEqual(JsonStore(self.store_path).draft_count(), 1)
        # 不得出现"已发布"结论
        self.assertNotIn("已发布", result.item.message)
        self.assertNotIn("published", result.item.message.lower())

    def test_repeat_submit_is_idempotent(self):
        self._run("M01")
        second = self._run("M01")
        self.assertEqual(second.item.draft_status.value, "SAVED_IDEMPOTENT")
        self.assertEqual(JsonStore(self.store_path).draft_count(), 1)

    def test_missing_info_does_not_save(self):
        result = self._run("M02")
        self.assertEqual(result.status.value, "NEEDS_INFO")
        self.assertEqual(result.item.audit_status.value, "NEED_INFO")
        self.assertEqual(result.item.draft_status.value, "NOT_SAVED")
        self.assertFalse(os.path.exists(self.store_path))
        self.assertTrue(any(p.kind == "missing_info" for p in result.item.pending_items))

    def test_rule_warn_saves_with_pending(self):
        result = self._run("M03")
        self.assertEqual(result.item.audit_status.value, "WARN")
        self.assertTrue(any(p.kind == "risk_warn" for p in result.item.pending_items))
        self.assertEqual(result.item.draft_status.value, "SAVED_CREATED")

    def test_block_rule_blocks_without_save(self):
        result = self._run("M04")
        self.assertEqual(result.status.value, "BLOCKED")
        self.assertEqual(result.item.draft_status.value, "NOT_SAVED")
        self.assertFalse(os.path.exists(self.store_path))

    def test_save_failure_is_reported_and_not_persisted(self):
        result = self._run("M01", fail_save=True)
        self.assertEqual(result.status.value, "FAILED")
        self.assertEqual(result.item.draft_status.value, "FAILED")
        self.assertFalse(os.path.exists(self.store_path))

    def test_step_limit_returns_not_finished(self):
        result = self._run("M01", max_steps=2)
        self.assertEqual(result.status.value, "LIMIT_REACHED")
        self.assertEqual(result.item.draft_status.value, "NOT_SAVED")

    def test_unknown_material_is_reported(self):
        result = self._run("M99")
        self.assertEqual(result.status.value, "NEEDS_INFO")
        self.assertEqual(result.item.draft_status.value, "NOT_SAVED")

    def test_every_step_records_tool_and_rationale(self):
        result = self._run("M01")
        self.assertGreaterEqual(len(result.steps), 5)
        for step in result.steps:
            self.assertTrue(step.action)
            self.assertTrue(step.rationale)
            self.assertIsInstance(step.args, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
