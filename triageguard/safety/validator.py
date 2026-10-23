import re

from triageguard.config import Config
from triageguard.knowledge.retriever import Hit

# URLs, emails, money, percentages and numbers of 2+ digits must come from the cited text.
FACTS = re.compile(r"https?://\S+|[\w.+-]+@[\w-]+\.[\w.]+|\$\d[\d,.]*|\d[\d,.]*%|\d{2,}[\d,.]*")


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def validate(gen: dict, hits: list[Hit], sensitive: bool, cfg: Config) -> tuple[str, str] | None:
    """Return (reason_code, reason) if the reply must be escalated, else None."""
    fail = lambda why: ("validation_failed", why)
    citations = gen["citations"]
    if not gen["answer"].strip() or not citations:
        return fail("missing answer or citations")

    by_id = {h.chunk.id: h for h in hits}
    cited = []
    for c in citations:
        if not isinstance(c, dict):
            return fail("malformed citation")
        hit = by_id.get(c.get("chunk_id"))
        if hit is None:
            return fail(f"citation to unretrieved chunk: {c.get('chunk_id')}")
        quote = _norm(str(c.get("quote", "")))
        if len(quote.split()) < 3 or quote not in _norm(hit.chunk.text):
            return fail(f"quote not found verbatim in {hit.chunk.id}")
        cited.append(hit)

    support = " ".join(h.chunk.text for h in cited)
    for fact in FACTS.findall(gen["answer"]):
        if fact.rstrip(".,") not in support:
            return fail(f"detail not in cited text: {fact}")

    if sensitive:
        policy = [h for h in cited if h.chunk.type == "policy"]
        if not policy or max(h.score for h in policy) < cfg.min_sensitive_score:
            return "sensitive_not_covered", "sensitive topic without a strongly matching policy article"
    return None
