"""Heading-aware markdown chunker for the knowledge base."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_WORDS = 180
OVERLAP_WORDS = 25
MIN_WORDS = 40

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    chunk_id: str
    title: str
    heading: str
    text: str

    @property
    def embed_text(self) -> str:
        """What gets embedded: title and heading give the encoder context the body may lack."""
        return f"{self.title}\n{self.text}"


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    match = _FRONT.match(raw)
    if not match:
        return meta, raw
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            meta[key.strip()] = value.strip()
    return meta, raw[match.end() :]


def _sections(body: str) -> list[tuple[str, str]]:
    """Split on markdown headings -> [(heading, text-including-heading-line)]."""
    matches = list(_HEADING.finditer(body))
    if not matches:
        return [("", body.strip())]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[m.start() : end].strip()
        if m.group(1) == "#":  # the H1 repeats the title; keep only if it has body text
            if len(text.split("\n", 1)) < 2 or not text.split("\n", 1)[1].strip():
                continue
        out.append((m.group(2).strip(), text))
    return out


def _split_long(text: str, max_words: int, overlap: int) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    step = max_words - overlap
    return [
        " ".join(words[i : i + max_words])
        for i in range(0, len(words), step)
        if i == 0 or i + overlap < len(words)
    ]


def chunk_document(
    raw: str, *, fallback_id: str, max_words: int = MAX_WORDS, overlap: int = OVERLAP_WORDS
) -> list[Chunk]:
    """Pack consecutive sections into chunks of at most `max_words`, never splitting a section
    unless it alone exceeds the limit (then split with `overlap` words of overlap)."""
    meta, body = parse_frontmatter(raw)
    doc_id = meta.get("id", fallback_id)
    title = meta.get("title", doc_id)
    pieces: list[tuple[str, str]] = []
    for heading, text in _sections(body):
        for part in _split_long(text, max_words, overlap):
            pieces.append((heading, part))

    groups: list[list[tuple[str, str]]] = []
    size = 0
    for heading, text in pieces:
        n = len(text.split())
        if groups and size + n <= max_words:
            groups[-1].append((heading, text))
            size += n
        else:
            groups.append([(heading, text)])
            size = n
    # fold a tiny trailing group (e.g. the tail of a split section) into its predecessor
    if len(groups) > 1 and sum(len(t.split()) for _, t in groups[-1]) < MIN_WORDS:
        groups[-2].extend(groups.pop())
    return [
        Chunk(
            doc_id=doc_id,
            chunk_id=f"{doc_id}#{i}",
            title=title,
            heading=" / ".join(h for h, _ in g if h),
            text="\n\n".join(t for _, t in g),
        )
        for i, g in enumerate(groups)
    ]


def load_kb(kb_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        chunks.extend(chunk_document(path.read_text(encoding="utf-8"), fallback_id=path.stem))
    return chunks
