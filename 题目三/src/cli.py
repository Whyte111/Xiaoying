"""CLI 入口：python -m src.cli ask "<问题>" [--offline --script <path>]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Agent
from .config import ROOT, Config
from .corpus import Corpus
from .faulty import FaultyRetriever
from .llm import DeepSeekClient, FakeLLM
from .retrieval import Retriever
from .session import Session
from .trace import Tracer

MAX_QUESTION_CHARS = 1000


def _render(answer) -> str:
    lines = [f"状态: {answer.status}", f"回答: {answer.text}"]
    if answer.citations:
        lines.append("引用:")
        for c in answer.citations:
            lines.append(f"  - {c.doc_id} v{c.version_date} §{c.section}")
            lines.append(f"    \"{c.quote}\"")
    if answer.gaps:
        lines.append("缺口:")
        for g in answer.gaps:
            lines.append(f"  - {g}")
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    # 被管道/重定向时统一 UTF-8，避免 Windows 控制台代码页把中文写坏
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(prog="qa-agent", description="企业知识问答 Agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ask = sub.add_parser("ask", help="提问；多个问题按顺序作为多轮对话")
    p_ask.add_argument("question", nargs="+", help="问题文本")
    p_ask.add_argument("--offline", action="store_true", help="离线模式，用脚本回放替代真实模型")
    p_ask.add_argument("--script", help="离线脚本 JSON 路径")
    p_ask.add_argument("--trace-out", help="trace 输出路径 (jsonl)")
    p_ask.add_argument("--steps", type=int, default=None, help="覆盖每轮最大步数")
    p_ask.add_argument("--fault", choices=["search"],
                       help="故障注入：让指定工具前 N 次调用失败（验证失败路径）")
    p_ask.add_argument("--fail-times", type=int, default=1, dest="fail_times",
                       help="故障注入的失败次数，默认 1")
    args = parser.parse_args(argv)

    cfg = Config.load()
    if args.steps is not None:
        if args.steps <= 0:
            print("--steps 必须为正整数", file=sys.stderr)
            return 2
        cfg.max_steps = args.steps

    for question in args.question:
        if not question.strip():
            print("问题不能为空", file=sys.stderr)
            return 2
        if len(question) > MAX_QUESTION_CHARS:
            print(f"问题过长（{len(question)} 字），上限 {MAX_QUESTION_CHARS} 字",
                  file=sys.stderr)
            return 2

    corpus = Corpus.load(cfg.data_dir)
    if len(corpus) == 0:
        print(f"data 目录没有可用文档：{cfg.data_dir}", file=sys.stderr)
        return 2

    if args.offline:
        if not args.script:
            print("离线模式需要 --script 指定回放脚本", file=sys.stderr)
            return 2
        try:
            script = json.loads(Path(args.script).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            print(f"离线脚本读取失败：{e}", file=sys.stderr)
            return 2
        if not isinstance(script, list):
            print("离线脚本必须是 JSON 数组", file=sys.stderr)
            return 2
        llm = FakeLLM(script)
    else:
        llm = DeepSeekClient(cfg)

    trace_path = Path(args.trace_out) if args.trace_out else None
    try:
        tracer = Tracer(trace_path, allowed_roots=[cfg.runs_dir, ROOT / "examples"])
    except (ValueError, OSError) as e:
        print(f"trace 输出不可用：{e}", file=sys.stderr)
        return 2
    retriever = Retriever(corpus)
    if args.fault:
        retriever = FaultyRetriever(retriever, times=args.fail_times)
    agent = Agent(cfg, llm, corpus, retriever, tracer)
    session = Session()

    multi = len(args.question) > 1
    for i, question in enumerate(args.question, 1):
        if multi:
            print(f"\n=== 第 {i} 轮 ===")
        answer = agent.run(question, session, turn=i)
        session.add(question, answer)
        print(_render(answer))

    tracer.close()
    if trace_path:
        print(f"trace: {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
