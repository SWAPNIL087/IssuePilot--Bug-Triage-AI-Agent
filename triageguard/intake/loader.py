import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from triageguard.frontmatter import split_front_matter

SUPPORTED = {".txt", ".md", ".json"}


class TicketError(Exception):
    pass


@dataclass
class Ticket:
    id: str
    body: str
    subject: str = ""
    customer: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}" if self.subject else self.body


def safe_id(value: str) -> str:
    return re.sub(r"[^\w.-]", "_", value)


def load_ticket(path: Path, max_bytes: int) -> Ticket:
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED:
        raise TicketError(f"unsupported file type: {path.suffix or 'none'}")
    try:
        if path.stat().st_size > max_bytes:
            raise TicketError(f"file larger than {max_bytes} bytes")
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise TicketError(f"unreadable file: {e}") from e

    if path.suffix.lower() == ".json":
        try:
            meta = json.loads(raw)
        except ValueError as e:
            raise TicketError(f"invalid JSON: {e}") from e
        if not isinstance(meta, dict):
            raise TicketError("JSON ticket must be an object")
        body = meta.get("body") or "\n\n".join(
            str(m.get("body", "")) for m in meta.get("messages", []) if isinstance(m, dict)
        )
    else:
        meta, body = split_front_matter(raw)

    body = str(body).strip()
    if not body:
        raise TicketError("empty ticket")
    return Ticket(
        id=safe_id(str(meta.get("id") or path.stem)),
        body=body,
        subject=str(meta.get("subject", "")),
        customer=str(meta.get("customer", "")),
        meta=meta,
    )
