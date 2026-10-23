import re
from dataclasses import dataclass
from pathlib import Path

from triageguard.frontmatter import split_front_matter


@dataclass(frozen=True)
class Chunk:
    id: str
    title: str
    section: str
    type: str
    text: str


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def chunk_article(path: Path, max_words: int) -> list[Chunk]:
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    title, kind = meta.get("title", path.stem), meta.get("type", "faq")

    sections, heading, buf = [], title, []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append((heading, text))
        buf.clear()

    for line in body.splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if m:
            flush()
            heading = m.group(1).strip()
        else:
            buf.append(line)
    flush()

    chunks, seen = [], set()
    for heading, text in sections:
        parts, cur, words = [], [], 0
        for para in re.split(r"\n\s*\n", text):
            n = len(para.split())
            if cur and words + n > max_words:
                parts.append("\n\n".join(cur))
                cur, words = [], 0
            cur.append(para)
            words += n
        parts.append("\n\n".join(cur))
        for part in parts:
            base = cid = f"{path.stem}#{_slug(heading)}"
            n = 2
            while cid in seen:
                cid, n = f"{base}-{n}", n + 1
            seen.add(cid)
            chunks.append(Chunk(cid, title, heading, kind, part))
    return chunks


def load_docs(docs_dir: Path, max_words: int) -> list[Chunk]:
    files = sorted(p for p in Path(docs_dir).glob("*") if p.suffix in {".md", ".txt"})
    chunks = [c for f in files for c in chunk_article(f, max_words)]
    if not chunks:
        raise ValueError(f"no help articles found in {docs_dir}")
    return chunks
