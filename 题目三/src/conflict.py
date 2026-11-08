"""冲突检测：同一主题下不同文档给出不同数值时判定为冲突。

判定规则（确定性、可单测）：
  1. 主题相同：小节标题去序号后相同，**或** 正文实词重叠度 >= 0.30；
  2. 且两者在同一"单位族"（时长 / 金额 / 百分比）上都抽到了数值，且数值集合不同。

为什么要加正文重叠这条：只看标题时，评审把「## 1. 报销时限」改成「## 1. 提交要求」
就完全测不出冲突，系统会擅自回答 15 天。正文重叠不依赖标题。

已知限制：改写幅度大到正文用词也完全更换时仍会漏报；阈值 0.30 是在当前 3 篇语料上
实测标定的（真正的冲突对 0.36，最大的非冲突对 0.22），语料变大后需重新标定。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .corpus import DocSegment
from .retrieval import tokenize

CONTENT_OVERLAP_THRESHOLD = 0.30

_UNIT_FAMILY = {
    "个自然日": "duration", "自然日": "duration",
    "个工作日": "duration", "工作日": "duration",
    "天": "duration", "日": "duration",
    "元": "money",
    "%": "percent", "％": "percent",
}
# 同义单位先归一再比对，否则「30 个自然日」和「30 天」会被误判成冲突
_CANONICAL_UNIT = {
    "个自然日": "天", "自然日": "天", "日": "天", "天": "天",
    "个工作日": "工作日", "工作日": "工作日",
    "元": "元", "%": "%", "％": "%",
}
_UNIT_PATTERN = "|".join(sorted(_UNIT_FAMILY, key=len, reverse=True))
_NUM = re.compile(rf"(\d+(?:\.\d+)?)\s*({_UNIT_PATTERN})")
_TITLE_PREFIX = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)\s*[\.、\)）]\s*")


def norm_title(title: str) -> str:
    return _TITLE_PREFIX.sub("", title).strip()


def extract_facts(text: str) -> "Dict[str, List[str]]":
    """按单位族归集数值，单位先归一，例：{'duration': ['30天'], 'money': ['600元']}。"""
    facts: "Dict[str, List[str]]" = {}
    for num, unit in _NUM.findall(text):
        canon = _CANONICAL_UNIT[unit]
        facts.setdefault(_UNIT_FAMILY[unit], []).append(f"{num}{canon}")
    return {k: sorted(set(v)) for k, v in facts.items()}


def extract_values(text: str) -> "List[str]":
    return sorted(v for vals in extract_facts(text).values() for v in vals)


def _same_topic(a: DocSegment, b: DocSegment) -> bool:
    if norm_title(a.section_title) == norm_title(b.section_title):
        return True
    ta, tb = set(tokenize(a.text)), set(tokenize(b.text))
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= CONTENT_OVERLAP_THRESHOLD


def _conflict_family(a: DocSegment, b: DocSegment) -> "str | None":
    fa, fb = extract_facts(a.text), extract_facts(b.text)
    for family in sorted(set(fa) & set(fb)):
        if set(fa[family]) != set(fb[family]):
            return family
    return None


@dataclass
class Conflict:
    topic: str
    segments: "List[DocSegment]" = field(default_factory=list)

    def values_by_doc(self) -> "Dict[str, List[str]]":
        out: "Dict[str, List[str]]" = {}
        for seg in self.segments:
            vals = extract_values(seg.text)
            if vals:
                out[seg.doc_id] = vals
        return out

    def docs(self) -> "List[str]":
        return sorted({s.doc_id for s in self.segments})

    def describe(self) -> str:
        pairs = [f"{d}: {'/'.join(v)}" for d, v in sorted(self.values_by_doc().items())]
        return f"{'/'.join(self.docs())} 对「{self.topic}」的规定不一致 -> " + "；".join(pairs)

    def question(self) -> str:
        pairs = [f"{d}：{'/'.join(v)}" for d, v in sorted(self.values_by_doc().items())]
        return (f"检测到 {'、'.join(self.docs())} 对「{self.topic}」的规定不一致（"
                + "；".join(pairs) + "），请问应适用哪一份制度？")

    def key(self) -> str:
        return f"{norm_title(self.topic)}|{','.join(self.docs())}"


def detect(segments: "List[DocSegment]") -> "List[Conflict]":
    pairs: "List[DocSegment]" = []
    seen = set()
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            a, b = segments[i], segments[j]
            if a.doc_id == b.doc_id:
                continue
            if (id(a), id(b)) in seen:
                continue
            if not _same_topic(a, b):
                continue
            if _conflict_family(a, b) is None:
                continue
            seen.add((id(a), id(b)))
            pairs.extend([a, b])

    # 把共享片段的冲突对合并成一个冲突对象
    clusters: "List[List[DocSegment]]" = []
    for k in range(0, len(pairs), 2):
        a, b = pairs[k], pairs[k + 1]
        overlapping = [c for c in clusters if a in c or b in c]
        if not overlapping:
            clusters.append([a, b])
            continue
        base = overlapping[0]
        for s in (a, b):
            if s not in base:
                base.append(s)
        for c in overlapping[1:]:
            for s in c:
                if s not in base:
                    base.append(s)
            clusters.remove(c)

    conflicts: "List[Conflict]" = []
    for cluster in clusters:
        titles = list(dict.fromkeys(norm_title(s.section_title) for s in cluster))
        conflicts.append(Conflict(topic=" / ".join(titles), segments=cluster))
    return conflicts


def in_play(cited_doc_ids: "List[str]", conflicts: "List[Conflict]") -> "List[Conflict]":
    """只对"最终答案引用了冲突文档"的情况强制澄清，避免误伤无关问题。"""
    cited = set(cited_doc_ids)
    return [c for c in conflicts if cited & set(c.docs())]
