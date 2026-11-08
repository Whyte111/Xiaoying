"""语料加载与切分：保留字符偏移，供引用校验使用。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DocSegment:
    doc_id: str
    version_date: str
    section_id: str
    section_title: str
    text: str
    start: int
    end: int
    source_path: str


@dataclass
class Document:
    doc_id: str
    title: str
    version_date: str
    body: str
    source_path: str
    segments: "list[DocSegment]" = field(default_factory=list)


_HEADING = re.compile(r"(?m)^##\s+(.+)$")


def _split_front_matter(text: str) -> "tuple[dict[str, str], str]":
    meta: "dict[str, str]" = {}
    if not text.lstrip().startswith("---"):
        return meta, text
    stripped = text.lstrip()
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return meta, text
    for line in parts[1].splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    return meta, parts[2].lstrip("\n")


def _load_one(path: Path) -> Document:
    # utf-8-sig：容忍 Windows 记事本保存的 BOM，否则 front-matter 会解析失败且静默降级
    raw = path.read_text(encoding="utf-8-sig")
    meta, body = _split_front_matter(raw)
    doc = Document(
        doc_id=meta.get("doc_id", path.stem),
        title=meta.get("title", path.stem),
        version_date=meta.get("version_date", "unknown"),
        body=body,
        source_path=str(path),
    )
    marks = [(m.start(), m.group(1).strip()) for m in _HEADING.finditer(body)]
    if not marks:
        doc.segments.append(
            DocSegment(doc.doc_id, doc.version_date, f"{doc.doc_id}#1", doc.title,
                       body, 0, len(body), doc.source_path)
        )
        return doc
    for idx, (pos, title) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(body)
        line_end = body.find("\n", pos)
        content_start = line_end + 1 if 0 <= line_end < end else end
        text = body[content_start:end].strip()
        doc.segments.append(
            DocSegment(doc.doc_id, doc.version_date, f"{doc.doc_id}#{idx + 1}",
                       title, text, content_start, end, doc.source_path)
        )
    return doc


class Corpus:
    def __init__(self) -> None:
        self.documents: "list[Document]" = []
        self.segments: "list[DocSegment]" = []
        self._by_id: "dict[str, Document]" = {}

    @classmethod
    def load(cls, data_dir: Path) -> "Corpus":
        c = cls()
        for path in sorted(Path(data_dir).glob("*.md")):
            doc = _load_one(path)
            c.documents.append(doc)
            c.segments.extend(doc.segments)
            c._by_id[doc.doc_id] = doc
        return c

    def doc(self, doc_id: str) -> "Document | None":
        return self._by_id.get(doc_id)

    def __len__(self) -> int:
        return len(self.documents)
