"""The bounded action flow: pick an approved runbook, validate, authorize, then propose or execute.
The LLM only supplies the runbook id and parameter values; everything here is plain code."""
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from triageguard.actions.runbook import ActionError, Runbook
from triageguard.intake.loader import Ticket

APPROVAL_TTL_S = 24 * 3600
STRICTNESS = {"shadow": 0, "approval": 1, "auto": 2}


@dataclass
class Actions:
    runbooks: dict[str, Runbook]
    tools: object  # anything with .call(runbook_id, params) -> {"rows": [...], "runbook_hash": ...}
    secret: str    # signs approval tokens


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fill(template: str, *sources: dict) -> str:
    merged = {}
    for s in sources:
        merged.update(s)
    return template.format_map(_Safe({k: str(v) for k, v in merged.items()}))


def _stop(d, code: str, reason: str):
    d.decision, d.reason_code, d.reason = "ESCALATE", code, reason
    return d


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_token(book: Runbook, params: dict, secret: str) -> str:
    body = _b64(json.dumps({"rb": book.id, "p": params, "exp": int(time.time()) + APPROVAL_TTL_S}).encode())
    return f"{body}.{_b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())}"


def read_token(token: str, secret: str) -> tuple[str, dict]:
    body, _, sig = token.partition(".")
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ActionError("invalid approval token")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if data["exp"] < time.time():
        raise ActionError("approval token expired")
    return data["rb"], data["p"]


def _system_params(book: Runbook, ticket: Ticket, values: dict) -> dict:
    key = "|".join([ticket.id, book.id] + [values[p.name] for p in book.params_from("ticket")])
    return {
        "request_id": hashlib.sha256(key.encode()).hexdigest()[:20],  # same ticket + same request = same id
        "ticket_id": ticket.id,
        "runbook_id": book.id,
        "runbook_version": book.version,
        "runbook_hash": book.hash,
    }


def _subset(book: Runbook, values: dict) -> dict:
    return {p.name: values[p.name] for p in book.params if p.name in values}


def run_action(d, ticket: Ticket, analysis: dict, actions: Actions, log):
    """Fills in the Decision `d` for a ticket the analyzer classified as an action request."""
    book = actions.runbooks.get(analysis["action"])
    if book is None:
        return _stop(d, "unknown_action", f"no approved runbook named {analysis['action']!r}")
    log(f"    [action] runbook {book.id} (v{book.version}, {book.risk})")

    # The requester is the authenticated channel identity, never something the ticket text can set.
    values = {"requester_email": ticket.customer}
    if not next(p for p in book.params if p.name == "requester_email").check(ticket.customer):
        return _stop(d, "identity_unverified", "no verified requester email on the ticket")

    given = analysis["action_params"]
    for p in book.params_from("ticket"):
        value = given.get(p.name)
        if p.check(value):
            values[p.name] = str(value).strip()
        elif value in (None, "") or p.rule.startswith("regex"):
            d.decision, d.reason_code, d.reason = "NEEDS_INFO", "missing_information", f"missing or invalid: {p.name}"
            d.answer = book.missing or "Could you give me a few more details so I can help?"
            return d
        else:
            return _stop(d, "action_not_allowed", f"{p.name} value is not allowed: {str(value)[:40]!r}")
    d.action = {"runbook_id": book.id, "params": {k: v for k, v in values.items() if k != "requester_email"}}

    try:
        if book.risk == "read":
            call = actions.tools.call(book.id, _subset(book, values))
            d.decision, d.answer = "REPLY", _fill(book.reply, values, call["rows"][0])
            return d

        log("    [action] checking the order before changing it")
        check = actions.runbooks[book.precheck]
        row = actions.tools.call(check.id, _subset(check, values))["rows"][0]
    except ActionError as e:
        return _stop(d, "action_failed", str(e))

    for p in book.params:
        if p.source.startswith("db:"):
            values[p.name] = str(row[p.source[3:]])
            if not p.check(values[p.name]):
                return _stop(d, "action_failed", f"unexpected database value for {p.name}")
    old, new = values["expected_current_status"], values["new_status"]
    transition = book.transition_mode(old, new)
    if transition is None:
        return _stop(d, "transition_not_allowed", f"{old} -> {new} is not an approved transition")

    values.update(_system_params(book, ticket, values))
    full = _subset(book, values)
    mode = min(book.mode, transition, key=STRICTNESS.get)
    d.action.update(params={k: v for k, v in full.items() if k != "requester_email"}, mode=mode,
                    summary=f"move order {values['order_id']} from {old} to {new}")

    if mode == "shadow":
        return _stop(d, "action_shadow", f"shadow mode: would {d.action['summary']}")
    if mode == "approval":
        d.decision, d.reason_code, d.reason = "PROPOSED", "awaiting_approval", f"needs approval: {d.action['summary']}"
        d.answer = _fill(book.pending, values)
        d.action["approval_token"] = make_token(book, full, actions.secret)
        return d
    return _execute(d, book, full, actions)


def _execute(d, book: Runbook, full: dict, actions: Actions):
    try:
        actions.tools.call(book.id, full)
        check = actions.runbooks[book.precheck]
        row = actions.tools.call(check.id, _subset(check, full))["rows"][0]
    except ActionError as e:
        return _stop(d, "action_failed", str(e))
    if str(row["status"]) != full["new_status"]:
        return _stop(d, "verify_failed", "the order status does not match after the change; needs a human")
    d.decision, d.reason_code, d.reason = "EXECUTED", "", ""
    d.answer = _fill(book.reply, full)
    d.action = {"runbook_id": book.id, "params": {k: v for k, v in full.items() if k != "requester_email"},
                "executed": True}
    return d


def approve(d, token: str, actions: Actions):
    """Runs a proposal a human approved. The SQL's compare-and-set rejects it if the order changed since."""
    try:
        runbook_id, full = read_token(token, actions.secret)
        book = actions.runbooks[runbook_id]
    except (ActionError, KeyError, ValueError) as e:
        return _stop(d, "invalid_approval", str(e))
    if full.get("runbook_hash") != book.hash:
        return _stop(d, "invalid_approval", "the runbook changed since this was proposed")
    d.ticket_id = full["ticket_id"]
    return _execute(d, book, full, actions)
