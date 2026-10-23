sample:
<img width="2297" height="1722" alt="image" src="https://github.com/user-attachments/assets/17fb0495-c156-4498-b47d-f3fa04deb364" />


# TriageGuard

Reads support tickets from files, searches a local help-article corpus, and either **replies with cited answers** or **escalates** to a human. See [DESIGN.md](DESIGN.md).

## Live demo (private pilot)

Base URL: **https://issuepilot-bug-triage-ai-agent.onrender.com**

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | is it up? |
| `POST /tickets` | `Authorization: Bearer <token>` | submit a ticket, get `REPLY` or `ESCALATE` |
| `GET /docs` | none | interactive API page |

The ticket endpoint needs a token, which is not published here. Ask the maintainer (@SWAPNIL087) for one. The service runs on a free instance: it sleeps after 15 minutes idle, so the first request can take about a minute, and the LLM allows roughly 7 tickets per minute.

```
curl -X POST https://issuepilot-bug-triage-ai-agent.onrender.com/tickets \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"subject": "Password", "body": "I forgot my password, how do I reset it?"}'
```

**Bruno:** open the [bruno/](bruno) folder as a collection, choose the `render` environment, and set its secret variable `token`. Requests 1 to 5 cover health, a grounded reply, two escalations and an auth failure, each with a test.
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

Render builds the `Dockerfile` for you, so you don't need Docker installed locally. Render > New > Web Service > this repo > Language **Docker** > Instance type **Free** > set Health Check Path to `/health`. Add `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `TRIAGEGUARD_MODEL` and `TRIAGEGUARD_API_TOKEN` as environment variables on the service, never in the repo.

Free instances sleep after 15 minutes idle (the next request takes about a minute), have an ephemeral disk (read decisions from the response and the logs, not `output/`), and are not meant for production. Free tiers change often, so check Render's current free-instance docs. Hugging Face Spaces no longer offers Docker Spaces on the free plan.
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
