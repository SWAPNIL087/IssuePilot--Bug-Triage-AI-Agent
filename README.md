# TriageGuard

Reads support tickets from files, searches a local help-article corpus, and either **replies with cited answers** or **escalates** to a human. See [DESIGN.md](DESIGN.md).

## Setup

```
pip install -e .
copy .env.example .env      # then set OPENAI_API_KEY
```

## Use

```
triageguard --ticket tickets/inbox/t-1001.txt      # one ticket
triageguard --inbox tickets/inbox                  # all tickets; handled files move to tickets/processed|failed
triageguard --inbox tickets/inbox --debug --no-move
```

Ticket files: `.txt` / `.md` (optional front matter `id`, `subject`, `customer`) or `.json` (`{id, subject, body}` or `{messages: [{body}]}`).
Help articles: `.md` / `.txt` in `docs/` (optional front matter `title`, `type: policy | how-to | faq`).

Results go to `output/`: `<id>.result.json`, `<id>.reply.md` (reply) or `<id>.escalate.json` (escalation, also appended to `escalations.jsonl`).

## Test

```
python -m unittest discover -s tests      # no API key needed
python -m evals.run_evals                 # needs a key; fails if any ticket is replied to when it should escalate
```

Tune `TRIAGEGUARD_MIN_SCORE` / `TRIAGEGUARD_MIN_SENSITIVE_SCORE` (see `.env.example`) with the evals.

## Layout

```
triageguard/
├─ cli.py  __main__.py  config.py  pipeline.py   entry point, settings, orchestration
├─ frontmatter.py                                 shared `key: value` header parser
├─ intake/     loader.py                          ticket file → Ticket
├─ knowledge/  ingest.py  retriever.py            help docs → chunks → BM25 search
├─ ai/         client.py  analyzer.py  generator.py   the two LLM calls
├─ safety/     validator.py                       code guardrails on the LLM answer
└─ actions/    escalation.py  output.py           result files; escalation seam
docs/  tickets/{inbox,processed,failed}/  output/  evals/  tests/
```
