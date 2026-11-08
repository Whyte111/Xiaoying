"""调参辅助：打印所有跨文档片段的词重叠度，用于确定冲突判定阈值。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.corpus import Corpus
from src.retrieval import tokenize


def main() -> int:
    corpus = Corpus.load(Path("data"))
    segs = corpus.segments
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            if a.doc_id == b.doc_id:
                continue
            ta, tb = set(tokenize(a.text)), set(tokenize(b.text))
            if not ta or not tb:
                continue
            inter = ta & tb
            contain = len(inter) / min(len(ta), len(tb))
            jac = len(inter) / len(ta | tb)
            print(f"{a.doc_id}§{a.section_title[:8]:<8} x {b.doc_id}§{b.section_title[:8]:<8} "
                  f"contain={contain:.2f} jaccard={jac:.2f} 共有={sorted(inter)[:6]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
