import json
from dataclasses import asdict

from triageguard.config import Config
from triageguard.intake.loader import Ticket

HANDOFF = "Thanks for reaching out. I've passed your request to our support team, who will follow up shortly."


def escalate(ticket: Ticket, decision, cfg: Config) -> str:
    """Escalation seam: today it writes files; later it can call a real tool (MCP/API)."""
    record = {"ticket": asdict(ticket), "decision": asdict(decision), "customer_message": HANDOFF}
    (cfg.output / f"{ticket.id}.escalate.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    line = {k: getattr(decision, k) for k in ("ticket_id", "reason_code", "reason", "processed_at")}
    with cfg.log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    return HANDOFF
