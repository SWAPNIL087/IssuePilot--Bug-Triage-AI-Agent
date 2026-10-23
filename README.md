---
title: TriageGuard
sdk: docker
app_port: 8000
---

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

## Run as a service (HTTP API)

```
pip install -e .[api]
# add TRIAGEGUARD_API_TOKEN=<random secret, 16+ chars> to .env
uvicorn triageguard.api:app --port 8000
```

```
POST /tickets   Authorization: Bearer <token>   {"id": "t-1", "subject": "...", "body": "..."}
GET  /health
```

Response: `decision` (`REPLY` | `ESCALATE`), `customer_message`, `sources`, `reason_code`, `reason`. A `503` means the LLM was unavailable: treat it as "needs a human". The service refuses to start without a token.

### Docker (optional)

Docker packages the app and its dependencies into one image that runs the same everywhere.

```
docker compose up --build      # reads secrets from .env, serves http://localhost:8000
```

### Free hosting

Hosts build the `Dockerfile` for you, so you don't need Docker installed locally. Set `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `TRIAGEGUARD_MODEL` and `TRIAGEGUARD_API_TOKEN` as the host's secret/environment variables, never in the repo.

- **Render** (free web service): New > Web Service > this repo > Docker > Free instance. Sleeps after 15 minutes idle; the disk is ephemeral. Currently asks for a card at sign-up.
- **Hugging Face Spaces** (Docker SDK): free CPU, no card. The README header already sets `sdk: docker` and `app_port: 8000`; put the variables in the Space secrets.

Free hosts lose `output/` on restart, so read decisions from the response and the logs. Check each host's current free-tier terms.

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
