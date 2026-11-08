"""CLI 层测试：退出码是评审唯一能脚本化的接口，必须被锁住。

退出码约定：
    0 completed / 1 未完成 / 2 输入非法 / 3 运行期错误
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.cli import main

VALID = {
    "topic": "智能剪辑器",
    "audience": "个人创作者",
    "platform": "douyin",
    "target_duration_s": 30,
    "style": "简洁口播",
}


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, payload, raw=False):
        path = self.dir / name
        text = payload if raw else json.dumps(payload, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_returns_2(self):
        code, _, err = run_cli(["run", "--input", str(self.dir / "nope.json"), "--out", str(self.dir / "o")])
        self.assertEqual(code, 2)
        self.assertIn("不存在", err)

    def test_broken_json_returns_2(self):
        p = self._write("bad.json", '{"topic": ', raw=True)
        code, _, err = run_cli(["run", "--input", str(p), "--out", str(self.dir / "o")])
        self.assertEqual(code, 2)
        self.assertIn("不是合法 JSON", err)

    def test_wrong_type_returns_2(self):
        payload = dict(VALID, material_ids="P01")
        p = self._write("bad_type.json", payload)
        code, _, err = run_cli(["run", "--input", str(p), "--out", str(self.dir / "o")])
        self.assertEqual(code, 2)
        self.assertIn("material_ids", err)

    def test_bom_file_is_accepted(self):
        """Windows 常见：带 BOM 的 UTF-8 文件必须能读。"""
        p = self.dir / "bom.json"
        p.write_text(json.dumps(VALID, ensure_ascii=False), encoding="utf-8-sig")
        code, out, _ = run_cli(
            ["run", "--input", str(p), "--model", "mock", "--out", str(self.dir / "o")]
        )
        self.assertEqual(code, 0, out)

    def test_completed_run_returns_0_and_writes_trace(self):
        p = self._write("ok.json", VALID)
        out_dir = self.dir / "o"
        code, out, _ = run_cli(["run", "--input", str(p), "--model", "mock", "--out", str(out_dir)])
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "completed")
        traces = list(out_dir.glob("*.trace.jsonl"))
        self.assertEqual(len(traces), 1, "应恰好产出一个 trace")
        self.assertGreater(len(traces[0].read_text(encoding="utf-8").splitlines()), 1)

    def test_incomplete_run_returns_1(self):
        payload = dict(VALID, platform="")
        p = self._write("needs.json", payload)
        code, out, _ = run_cli(["run", "--input", str(p), "--model", "mock", "--out", str(self.dir / "o")])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "needs_input")

    def test_max_steps_is_clamped_in_trace(self):
        p = self._write("ok2.json", VALID)
        out_dir = self.dir / "o2"
        run_cli(
            ["run", "--input", str(p), "--model", "mock", "--out", str(out_dir), "--max-steps", "999999"]
        )
        first = json.loads(
            (list(out_dir.glob("*.trace.jsonl"))[0]).read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual(first["limits"]["max_steps"], 100)


if __name__ == "__main__":
    unittest.main()
