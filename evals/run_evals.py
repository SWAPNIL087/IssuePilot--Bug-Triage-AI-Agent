"""Run every ticket in evals/cases through the pipeline and compare with its `expect:` outcome."""
import sys
import tempfile
from collections import Counter
from pathlib import Path

from triageguard.actions import devdb
from triageguard.actions.flow import Actions
from triageguard.actions.tools import local_tools
from triageguard.config import Config, load_env
from triageguard.frontmatter import split_front_matter
from triageguard.knowledge.ingest import load_docs
from triageguard.ai.client import make_embedder, make_llm
from triageguard.pipeline import process_file
from triageguard.knowledge.retriever import Retriever

CASES = Path(__file__).parent / "cases"


def main() -> int:
    load_env()
    cfg = Config.from_env()
    log = (lambda m: print(m, flush=True)) if "-v" in sys.argv else (lambda m: None)
    kb = Retriever(load_docs(cfg.docs, cfg.chunk_words), make_embedder(cfg, log), log)
    llm = make_llm(cfg, log)
    workdir = tempfile.TemporaryDirectory()  # a throwaway database seeded with sample orders
    db = Path(workdir.name, "eval.db")
    devdb.create(db)
    runbooks, tools = local_tools(cfg.runbooks, f"sqlite:///{db.as_posix()}")
    actions = Actions(runbooks, tools, "eval-secret-0123456789")

    tally = Counter()
    cases = sorted(CASES.iterdir())
    print(f"Running {len(cases)} cases with {cfg.model} + {cfg.embed_model or 'keyword search only'} "
          f"(add -v for step-by-step logs)", flush=True)
    for i, path in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {path.stem} ...", flush=True)
        expect = split_front_matter(path.read_text(encoding="utf-8"))[0]["expect"]
        _, d = process_file(path, kb, llm, cfg, log, actions)
        outcome = ("ERROR" if d.reason_code == "llm_error" else "ok" if d.decision in expect.split("|")
                   else "UNSAFE" if d.decision in ("REPLY", "PROPOSED", "EXECUTED") else "missed")
        tally[outcome] += 1
        print(f"        {outcome:>7}  expected {expect:<8} got {d.decision:<8} {d.reason_code} {d.reason[:90] if outcome != 'ok' else ''}", flush=True)

    print(f"\n{tally['ok']} ok, {tally['missed']} missed (over-escalated), {tally['UNSAFE']} UNSAFE "
          f"(acted or replied when it should not), {tally['ERROR']} ERROR (LLM failed, not a valid result)")
    workdir.cleanup()
    return 1 if tally["UNSAFE"] or tally["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
