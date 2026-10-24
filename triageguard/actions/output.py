import json
from dataclasses import asdict

from triageguard.config import Config
from triageguard.actions.escalation import escalate
from triageguard.intake.loader import Ticket
from triageguard.pipeline import Decision
from triageguard.knowledge.retriever import Retriever


def sources(d: Decision, kb: Retriever) -> list[str]:
    seen = {}
    for c in d.citations:
        chunk = kb.by_id[c["chunk_id"]]
        seen[f"{chunk.title} › {chunk.section}"] = None
    return list(seen)


def save(ticket: Ticket, d: Decision, kb: Retriever, cfg: Config) -> str:
    """Write result files; return the customer-facing message."""
    cfg.output.mkdir(parents=True, exist_ok=True)
    (cfg.output / f"{d.ticket_id}.result.json").write_text(json.dumps(asdict(d), indent=2), encoding="utf-8")
    if d.decision == "ESCALATE":
        return escalate(ticket, d, cfg)
    if d.decision == "PROPOSED":  # a human must approve; the result file holds the approval token
        return d.answer
    reply = d.answer + ("\n\nSources:\n" + "\n".join(f"- {s}" for s in sources(d, kb)) + "\n" if d.citations else "\n")
    (cfg.output / f"{d.ticket_id}.reply.md").write_text(reply, encoding="utf-8")
    return d.answer
