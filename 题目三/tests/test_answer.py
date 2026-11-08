"""引用校验与状态裁决的单元测试。"""
from __future__ import annotations

import unittest

from src.answer import (ANSWERED, INSUFFICIENT, assemble, normalize)
from src.corpus import Corpus, DocSegment, Document

QUOTE = "一线城市住宿标准为每人每晚 600 元，二线城市为每人每晚 400 元，超出标准部分由个人承担。"
BODY = "## 2. 住宿标准\n" + QUOTE + "\n"


def make_corpus() -> Corpus:
    corpus = Corpus()
    doc = Document("D01", "差旅制度", "2024-03-01", BODY, "mem")
    doc.segments.append(DocSegment("D01", "2024-03-01", "D01#2", "2. 住宿标准",
                                   QUOTE, 0, len(QUOTE), "mem"))
    corpus.documents.append(doc)
    corpus.segments.extend(doc.segments)
    corpus._by_id["D01"] = doc
    return corpus


class TestCiteVerify(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = make_corpus()

    def test_verbatim_passes(self):
        ans = assemble({"answer": "一线城市每人每晚 600 元。",
                        "citations": [{"doc_id": "D01", "quote": QUOTE}]}, self.corpus)
        self.assertEqual(ans.status, ANSWERED)
        self.assertTrue(ans.citations[0].verified)

    def test_whitespace_tolerated(self):
        q = QUOTE.replace("，", "， ").replace("600 元", "600元")
        ans = assemble({"answer": "一线城市每人每晚 600 元。",
                        "citations": [{"doc_id": "D01", "quote": q}]}, self.corpus)
        self.assertEqual(ans.status, ANSWERED)

    def test_one_char_altered_rejected(self):
        q = QUOTE.replace("600", "800")
        ans = assemble({"answer": "一线城市每人每晚 800 元。",
                        "citations": [{"doc_id": "D01", "quote": q}]}, self.corpus)
        self.assertEqual(ans.status, INSUFFICIENT)
        self.assertTrue(any("逐字" in g for g in ans.gaps))

    def test_irrelevant_quote_rejected(self):
        ans = assemble({"answer": "年假为 7 天。",
                        "citations": [{"doc_id": "D01", "quote": QUOTE}]},
                       self.corpus, "年假几天")
        self.assertEqual(ans.status, INSUFFICIENT)
        self.assertTrue(any("无关" in g for g in ans.gaps))

    def test_paraphrased_answer_rescued_by_question(self):
        """模型把答案改写成口语、不复用原文措辞时，用问题词回退，不应误杀。"""
        ans = assemble({"answer": "住酒店一晚能报六百块。",
                        "citations": [{"doc_id": "D01", "quote": QUOTE}]},
                       self.corpus, "出差住宿标准是多少？")
        self.assertEqual(ans.status, ANSWERED)

    def test_no_citation_is_insufficient(self):
        ans = assemble({"answer": "年假 7 天。", "citations": []}, self.corpus)
        self.assertEqual(ans.status, INSUFFICIENT)

    def test_gaps_string_tolerated(self):
        ans = assemble({"answer": "x", "citations": [], "gaps": "缺资料"}, self.corpus)
        self.assertEqual(ans.gaps[0], "缺资料")

    def test_generic_word_does_not_rescue(self):
        """「员工」这类通用词只命中 1 个，不能把无关引用救回来。"""
        body = "本制度适用于公司全体正式员工因公出差产生的交通、住宿与市内交通费用。"
        corpus = make_corpus()
        corpus.documents[0].body += body
        ans = assemble({"answer": "年假为 7 天。",
                        "citations": [{"doc_id": "D01", "quote": body}]},
                       corpus, "员工年假有几天？")
        self.assertEqual(ans.status, INSUFFICIENT)

    def test_normalize_strips_whitespace(self):
        self.assertEqual(normalize(" 600 元\n"), "600元")


if __name__ == "__main__":
    unittest.main()
