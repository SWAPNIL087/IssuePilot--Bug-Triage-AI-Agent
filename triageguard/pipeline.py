import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from triageguard.ai.analyzer import analyze
from triageguard.config import Config
from triageguard.ai.generator import generate
from triageguard.ai.client import LLM, LLMError
from triageguard.intake.loader import Ticket, TicketError, load_ticket
from triageguard.knowledge.retriever import Retriever
from triageguard.safety.validator import validate

Log = Callable[[str], None]
ATTACHMENT = re.compile(r"\b(attached|attachments?|screenshots?)\b", re.I)


@dataclass
class Decision:
    ticket_id: str
    decision: str = "ESCALATE"
    reason_code: str = ""
    reason: str = ""
    answer: str = ""
    citations: list = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    retrieved: list = field(default_factory=list)
    model: str = ""
    processed_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))


def process(ticket: Ticket, kb: Retriever, llm: LLM, cfg: Config, log: Log = lambda m: None) -> Decision:
    d = Decision(ticket.id, model=cfg.model)

    def stop(code: str, reason: str) -> Decision:
        d.reason_code, d.reason = code, reason
        return d

    if ATTACHMENT.search(ticket.text):
        return stop("attachment_unreadable", "ticket refers to an attachment we cannot read")
    try:
        log("    [1/4] analyzing ticket")
        d.analysis = analyze(ticket, llm)
        log(f"          sensitive={d.analysis['sensitive']}, search query: {d.analysis['search_query']!r}")
        if d.analysis["wants_human"]:
            return stop("customer_requested_human", "customer asked for a human")
        log("    [2/4] searching help articles")
        hits = kb.search(f"{d.analysis['search_query']} {ticket.subject}", cfg.top_k)
        d.retrieved = [{"chunk_id": h.chunk.id, "score": round(h.score, 2)} for h in hits]
        best = f"{hits[0].chunk.id} (score {hits[0].score:.1f}, need {cfg.min_score})" if hits else "none"
        log(f"          best match: {best}")
        if not hits or hits[0].score < cfg.min_score:
            return stop("no_relevant_docs", "no help article matches the ticket")
        log("    [3/4] generating answer from the excerpts")
        gen = generate(ticket, hits, llm)
    except LLMError as e:
        return stop("llm_error", str(e))

    if gen["decision"] != "REPLY":
        return stop("partial_coverage", gen["reason"] or "docs do not fully answer the ticket")
    log("    [4/4] validating answer against the docs")
    if failure := validate(gen, hits, d.analysis["sensitive"], cfg):
        return stop(*failure)

    d.decision, d.answer, d.citations = "REPLY", gen["answer"].strip(), gen["citations"]
    return d


def process_file(path: Path, kb: Retriever, llm: LLM, cfg: Config, log: Log = lambda m: None) -> tuple[Ticket, Decision]:
    try:
        ticket = load_ticket(path, cfg.max_ticket_bytes)
    except TicketError as e:
        ticket = Ticket(id=Path(path).stem, body="")
        return ticket, Decision(ticket.id, reason_code="invalid_ticket", reason=str(e), model=cfg.model)
    return ticket, process(ticket, kb, llm, cfg, log)
