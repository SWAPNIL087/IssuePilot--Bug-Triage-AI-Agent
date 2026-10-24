import json
import re
import time
from typing import Callable

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

from triageguard.config import Config

LLM = Callable[[str, str], dict]
Embedder = Callable[[list[str]], list[list[float]]]
Log = Callable[[str], None]
RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)


class LLMError(Exception):
    pass


def _quiet(message: str) -> None:
    pass


def _retry_wait(error: Exception, attempt: int) -> float:
    """Honor a "retry in 50s" hint from the provider, else back off exponentially."""
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", str(error))
    return min(float(m.group(1)) + 1, 65.0) if m else 2.0 ** (attempt + 1)


def check(out, **fields: type) -> dict:
    """Validate an LLM JSON reply against {field: type}; never best-guess."""
    if not isinstance(out, dict) or any(not isinstance(out.get(k), t) for k, t in fields.items()):
        raise LLMError(f"malformed LLM output: {str(out)[:200]}")
    return out


def _client(cfg: Config) -> OpenAI:
    try:
        return OpenAI(timeout=cfg.timeout, max_retries=0)  # retries are done in _request so they can be logged
    except OpenAIError as e:
        raise LLMError(str(e)) from e


def _request(cfg: Config, log: Log, label: str, send: Callable[[], object]):
    """Run send() with logged retries; every failure becomes LLMError."""
    for attempt in range(cfg.retries + 1):
        log(f"        calling {label} (timeout {cfg.timeout:.0f}s)...")
        start = time.monotonic()
        try:
            out = send()
            log(f"        replied in {time.monotonic() - start:.1f}s")
            return out
        except RETRYABLE as e:
            if attempt == cfg.retries or "insufficient_quota" in str(e):
                raise LLMError(str(e)) from e
            wait = _retry_wait(e, attempt)
            log(f"        {type(e).__name__} after {time.monotonic() - start:.1f}s; "
                f"retry {attempt + 1}/{cfg.retries} in {wait:.0f}s")
            time.sleep(wait)
        except (OpenAIError, ValueError, TypeError) as e:
            raise LLMError(str(e)) from e
        except (AttributeError, IndexError) as e:
            raise LLMError(f"endpoint did not return a valid response: {e}") from e


def make_llm(cfg: Config, log: Log = _quiet) -> LLM:
    client = _client(cfg)

    def call(system: str, user: str) -> dict:
        def send():
            r = client.chat.completions.create(
                model=cfg.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return json.loads(r.choices[0].message.content)

        return _request(cfg, log, cfg.model, send)

    return call


def make_embedder(cfg: Config, log: Log = _quiet) -> Embedder | None:
    """Text -> vectors for semantic search. None when no embedding model is configured."""
    if not cfg.embed_model:
        return None
    client = _client(cfg)

    def embed(texts: list[str]) -> list[list[float]]:
        def send():
            vectors = []
            for i in range(0, len(texts), 100):
                r = client.embeddings.create(model=cfg.embed_model, input=texts[i:i + 100])
                vectors += [d.embedding for d in r.data]
            if len(vectors) != len(texts):
                raise ValueError("embedding count does not match input count")
            return vectors

        return _request(cfg, log, cfg.embed_model, send)

    return embed