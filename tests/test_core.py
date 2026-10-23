import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from triageguard.ai.analyzer import analyze
from triageguard.config import ROOT, Config
from triageguard.knowledge.ingest import chunk_article, load_docs
from triageguard.ai.client import LLMError
from triageguard.intake.loader import Ticket, TicketError, load_ticket
from triageguard.actions.output import save
from triageguard.pipeline import process, process_file
from triageguard.knowledge.retriever import Retriever

CFG = Config()
KB = Retriever(load_docs(ROOT / "docs", CFG.chunk_words))
RESET = KB.by_id["account-access#resetting-your-password"]
QUOTE = "We send a reset link that is valid for 60 minutes."


def fake_llm(analysis=None, generation=None):
    """Stand-in LLM: first call is the analyzer, second is the generator."""
    base = {"intent": "x", "topic": "x", "sensitive": False, "wants_human": False,
            "search_query": "reset password"}
    replies = [{**base, **(analysis or {})}, generation or {
        "decision": "REPLY", "answer": "Use the reset link; it is valid for 60 minutes.",
        "citations": [{"chunk_id": RESET.id, "quote": QUOTE}], "reason": ""}]

    def llm(system, user):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return llm


class TestIngestAndSearch(unittest.TestCase):
    def test_chunks_by_heading_with_metadata(self):
        self.assertEqual((RESET.title, RESET.section, RESET.type), ("Account Access", "Resetting your password", "how-to"))

    def test_long_section_is_split(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "a.md")
            p.write_text("# A\n\n" + "\n\n".join(["word " * 100] * 5), encoding="utf-8")
            self.assertEqual([c.id for c in chunk_article(p, 220)], ["a#a", "a#a-2", "a#a-3"])

    def test_search_ranks_relevant_article_first(self):
        self.assertEqual(KB.search("forgot password reset", 3)[0].chunk.id, RESET.id)
        self.assertEqual(KB.search("download invoice pdf", 1)[0].chunk.section, "Downloading invoices")

    def test_unrelated_query_scores_below_threshold(self):
        hits = KB.search("banana bread recipe", 1)
        self.assertTrue(not hits or hits[0].score < CFG.min_score)


class TestLoader(unittest.TestCase):
    def load(self, name, content):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, name)
            p.write_text(content, encoding="utf-8")
            return load_ticket(p, CFG.max_ticket_bytes)

    def test_txt_front_matter_and_json(self):
        t = self.load("a.txt", "---\nid: t/1\nsubject: Hi\n---\nbody")
        self.assertEqual((t.id, t.subject, t.body), ("t_1", "Hi", "body"))
        t = self.load("b.json", json.dumps({"messages": [{"body": "one"}, {"body": "two"}]}))
        self.assertEqual((t.id, t.body), ("b", "one\n\ntwo"))

    def test_rejects_bad_tickets(self):
        for name, content in [("a.txt", "  \n"), ("a.json", "[1]"), ("a.json", "{"), ("a.csv", "x")]:
            with self.assertRaises(TicketError, msg=name):
                self.load(name, content)
        with self.assertRaises(TicketError):
            with tempfile.TemporaryDirectory() as d:
                Path(d, "a.txt").write_text("x" * 50)
                load_ticket(Path(d, "a.txt"), 10)


class TestPipeline(unittest.TestCase):
    def run_ticket(self, body="How do I reset my password?", cfg=CFG, **llm_kwargs):
        return process(Ticket("t1", body), KB, fake_llm(**llm_kwargs), cfg)

    def test_grounded_reply(self):
        d = self.run_ticket()
        self.assertEqual((d.decision, d.reason_code), ("REPLY", ""))

    def test_fabricated_quote_escalates(self):
        gen = {"decision": "REPLY", "answer": "Done.", "reason": "",
               "citations": [{"chunk_id": RESET.id, "quote": "Resets are instant and free of charge."}]}
        self.assertEqual(self.run_ticket(generation=gen).reason_code, "validation_failed")

    def test_invented_detail_and_unknown_chunk_escalate(self):
        gen = {"decision": "REPLY", "answer": "The link is valid for 90 minutes.", "reason": "",
               "citations": [{"chunk_id": RESET.id, "quote": QUOTE}]}
        self.assertIn("90", self.run_ticket(generation=gen).reason)
        gen["citations"] = [{"chunk_id": "nope#x", "quote": QUOTE}]
        self.assertEqual(self.run_ticket(generation=gen).reason_code, "validation_failed")

    def test_sensitive_needs_policy_article(self):
        self.assertEqual(self.run_ticket(analysis={"sensitive": True}).reason_code, "sensitive_not_covered")

    def test_generator_escalation_and_gates(self):
        gen = {"decision": "ESCALATE", "answer": "", "citations": [], "reason": "not covered"}
        self.assertEqual(self.run_ticket(generation=gen).reason_code, "partial_coverage")
        self.assertEqual(self.run_ticket(analysis={"wants_human": True}).reason_code, "customer_requested_human")
        self.assertEqual(self.run_ticket(analysis={"search_query": "banana bread"}).reason_code, "no_relevant_docs")
        self.assertEqual(self.run_ticket("see attached screenshot").reason_code, "attachment_unreadable")

    def test_llm_failure_escalates(self):
        d = self.run_ticket(analysis=None, generation=LLMError("boom"))
        self.assertEqual(d.reason_code, "llm_error")

    def test_malformed_llm_output_escalates(self):
        with self.assertRaises(LLMError):
            analyze(Ticket("t", "x"), lambda s, u: {"intent": "x"})

    def test_invalid_file_and_saved_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = replace(CFG, output=Path(d, "out"), log=Path(d, "log.jsonl"))
            Path(d, "empty.txt").write_text("")
            ticket, dec = process_file(Path(d, "empty.txt"), KB, fake_llm(), cfg)
            self.assertEqual(dec.reason_code, "invalid_ticket")
            save(ticket, dec, KB, cfg)
            self.assertTrue(Path(d, "out", "empty.escalate.json").is_file())
            self.assertIn("invalid_ticket", Path(d, "log.jsonl").read_text())

            ticket, dec = self.run_ticket_pair(cfg)
            save(ticket, dec, KB, cfg)
            self.assertIn("Account Access › Resetting your password", Path(d, "out", "t1.reply.md").read_text(encoding="utf-8"))

    def run_ticket_pair(self, cfg):
        t = Ticket("t1", "How do I reset my password?")
        return t, process(t, KB, fake_llm(), cfg)


if __name__ == "__main__":
    unittest.main()
