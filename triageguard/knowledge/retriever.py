import math
import re
from collections import Counter
from typing import NamedTuple

from triageguard.knowledge.ingest import Chunk

K1, B = 1.5, 0.75
STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from how i if in is it me my of on or "
    "our so that the their there to us was we what when where which who will with you your".split()
)


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOPWORDS:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


class Hit(NamedTuple):
    chunk: Chunk
    score: float


class Retriever:
    """BM25 over help-article chunks."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.by_id = {c.id: c for c in chunks}
        self._tf = [Counter(tokens(f"{c.title} {c.section} {c.text}")) for c in chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = sum(self._len) / len(chunks)
        df = Counter(t for tf in self._tf for t in tf)
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int) -> list[Hit]:
        terms = set(tokens(query))
        hits = []
        for chunk, tf, length in zip(self.chunks, self._tf, self._len):
            score = sum(
                self._idf[t] * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * length / self._avg))
                for t in terms
                if t in tf
            )
            if score > 0:
                hits.append(Hit(chunk, score))
        return sorted(hits, key=lambda h: -h.score)[:k]
