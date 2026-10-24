import math
import re
from collections import Counter
from itertools import zip_longest
from typing import Callable, NamedTuple

from triageguard.ai.client import Embedder, LLMError
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


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


class Hit(NamedTuple):
    chunk: Chunk
    score: float       # BM25 keyword score
    sim: float = 0.0   # cosine similarity to the ticket (0 when semantic search is off)


class Retriever:
    """Hybrid search: BM25 keywords + embedding similarity, merged round-robin so the best keyword
    hit and the best semantic hit are always both candidates. Without an embedder (or if it fails)
    it is plain BM25."""

    def __init__(self, chunks: list[Chunk], embed: Embedder | None = None,
                 log: Callable[[str], None] = lambda m: None):
        self.chunks = chunks
        self.by_id = {c.id: c for c in chunks}
        self._tf = [Counter(tokens(f"{c.title} {c.section} {c.text}")) for c in chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = sum(self._len) / len(chunks)
        df = Counter(t for tf in self._tf for t in tf)
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

        self._embed, self._log, self._vectors = embed, log, []
        if embed:
            try:
                log("embedding help articles for semantic search...")
                self._vectors = [_unit(v) for v in embed([f"{c.title} > {c.section}\n{c.text}" for c in chunks])]
            except LLMError as e:
                log(f"semantic search disabled, using keyword search only: {e}")
                self._embed = None

    def _keyword_scores(self, query: str) -> list[float]:
        terms = set(tokens(query))
        return [
            sum(self._idf[t] * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * length / self._avg))
                for t in terms if t in tf)
            for tf, length in zip(self._tf, self._len)
        ]

    def _similarities(self, text: str) -> list[float] | None:
        if not self._embed:
            return None
        try:
            query = _unit(self._embed([text])[0])
        except LLMError as e:
            self._log(f"semantic search skipped for this ticket: {e}")
            return None
        return [sum(a * b for a, b in zip(query, v)) for v in self._vectors]

    def search(self, query: str, k: int, text: str = "") -> list[Hit]:
        """query: keywords for BM25. text: the ticket itself, embedded for semantic search."""
        keyword = self._keyword_scores(query)
        semantic = self._similarities(text or query)
        rankings = [[i for i in sorted(range(len(keyword)), key=lambda i: -keyword[i]) if keyword[i] > 0]]
        if semantic:
            rankings.append(sorted(range(len(semantic)), key=lambda i: -semantic[i]))
        merged = []
        for tier in zip_longest(*(ranking[:k] for ranking in rankings)):
            merged += [i for i in tier if i is not None and i not in merged]
        top = merged[:k]
        return [Hit(self.chunks[i], keyword[i], semantic[i] if semantic else 0.0) for i in top]