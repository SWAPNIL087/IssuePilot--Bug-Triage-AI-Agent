"""HTTP service: POST a ticket, get a REPLY or ESCALATE decision back."""
import hmac
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from triageguard.actions.escalation import HANDOFF
from triageguard.actions.output import sources
from triageguard.ai.client import make_llm
from triageguard.config import Config, load_env
from triageguard.intake.loader import Ticket, safe_id
from triageguard.knowledge.ingest import load_docs
from triageguard.knowledge.retriever import Retriever
from triageguard.pipeline import process

ERRORS = {"invalid_ticket", "llm_error"}


def log(message: str) -> None:
    print(message, flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
    token = os.environ.get("TRIAGEGUARD_API_TOKEN", "")
    if len(token) < 16:  # fail closed: never run an open endpoint
        raise RuntimeError("Set TRIAGEGUARD_API_TOKEN to a secret of at least 16 characters")
    app.state.token = token
    app.state.cfg = cfg = Config.from_env()
    app.state.kb = Retriever(load_docs(cfg.docs, cfg.chunk_words))
    app.state.llm = make_llm(cfg, log)
    log(f"TriageGuard API ready: {len(app.state.kb.chunks)} chunks, model {cfg.model}")
    yield


app = FastAPI(title="TriageGuard", lifespan=lifespan)


class TicketIn(BaseModel):
    id: str | None = None
    subject: str = Field("", max_length=300)
    customer: str = Field("", max_length=300)
    body: str = Field(max_length=20000)


def require_token(authorization: str = Header("")) -> None:
    expected = f"Bearer {app.state.token}"
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/tickets", dependencies=[Depends(require_token)])
def create_ticket(t: TicketIn) -> dict:
    if not t.body.strip():
        raise HTTPException(status_code=422, detail="empty ticket body")
    ticket = Ticket(safe_id(t.id or uuid.uuid4().hex[:12]), t.body.strip(), t.subject, t.customer)
    state = app.state
    log(f"ticket {ticket.id}")
    d = process(ticket, state.kb, state.llm, state.cfg, log)
    message = d.answer if d.decision == "REPLY" else HANDOFF  # nothing is written to disk; the response is the record
    log(f"ticket {ticket.id} -> {d.decision} {d.reason_code}")
    if d.reason_code in ERRORS:
        raise HTTPException(status_code=503, detail=f"{d.reason_code}: {d.reason}")
    return {
        "ticket_id": ticket.id,
        "decision": d.decision,
        "customer_message": message,
        "sources": sources(d, state.kb),
        "reason_code": d.reason_code,
        "reason": d.reason,
    }
