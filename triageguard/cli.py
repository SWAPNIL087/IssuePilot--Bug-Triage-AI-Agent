import argparse
import os
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

from triageguard.config import Config, load_env
from triageguard.knowledge.ingest import load_docs
from triageguard.ai.client import LLMError, make_embedder, make_llm
from triageguard.intake.loader import SUPPORTED
from triageguard.actions.output import save, sources
from triageguard.pipeline import process_file
from triageguard.knowledge.retriever import Retriever

ERRORS = {"invalid_ticket", "llm_error"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="triageguard", description="Reply from the help docs or escalate.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ticket", type=Path, help="process one ticket file")
    mode.add_argument("--inbox", type=Path, help="process every ticket file in a folder")
    ap.add_argument("--docs", type=Path, help="help-article folder (default: docs/)")
    ap.add_argument("--output", type=Path, help="results folder (default: output/)")
    ap.add_argument("--no-move", action="store_true", help="leave inbox files in place")
    ap.add_argument("--debug", action="store_true", help="show retrieved chunks and scores")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    cfg = Config.from_env()
    cfg = replace(cfg, docs=args.docs or cfg.docs, output=args.output or cfg.output)

    if args.ticket:
        if not args.ticket.is_file():
            return _fail(f"ticket not found: {args.ticket}")
        files = [args.ticket]
    elif args.inbox.is_dir():
        files = sorted(p for p in args.inbox.iterdir() if p.is_file())
    else:
        return _fail(f"inbox not found: {args.inbox}")

    def log(message: str) -> None:
        print(message, flush=True)

    try:
        kb = Retriever(load_docs(cfg.docs, cfg.chunk_words), make_embedder(cfg, log), log)
        llm = make_llm(cfg, log)
    except (ValueError, LLMError) as e:
        return _fail(str(e))

    articles = len({c.id.split("#")[0] for c in kb.chunks})
    log(f"TriageGuard ready. {articles} articles loaded. Model: {cfg.model}. {len(files)} ticket(s) to process.\n")

    counts = Counter()
    for i, path in enumerate(files, 1):
        if path.suffix.lower() not in SUPPORTED:
            log(f"[{i}/{len(files)}] {path.name}  skipped (unsupported file type)")
            continue
        log(f"[{i}/{len(files)}] {path.name}")
        ticket, d = process_file(path, kb, llm, cfg, log)
        message = save(ticket, d, kb, cfg)
        counts[d.decision] += 1
        counts["errors"] += d.reason_code in ERRORS
        detail = "; ".join(sources(d, kb)) if d.decision == "REPLY" else f"{d.reason_code}: {d.reason}"
        log(f"    → {d.decision}  ({detail})")
        if args.ticket or args.debug:
            log(f"    {message}")
        if args.debug:
            for r in d.retrieved:
                    log(f"    keyword {r['keyword']:>5}  semantic {r['semantic']:.2f}  {r['chunk_id']}")
        log("")
        if args.inbox and not args.no_move:
            dest = path.parent.parent / ("failed" if d.reason_code in ERRORS else "processed")
            dest.mkdir(exist_ok=True)
            os.replace(path, dest / path.name)

    print(f"\nSummary: {counts['REPLY']} replied, {counts['ESCALATE']} escalated, "
          f"{counts['errors']} errors. Results in {cfg.output}")
    return 1 if counts["errors"] else 0


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2
