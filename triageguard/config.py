import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Config:
    docs: Path = ROOT / "docs"
    output: Path = ROOT / "output"
    log: Path = ROOT / "escalations.jsonl"
    model: str = "gpt-4o-mini"
    top_k: int = 5
    min_score: float = 3.0
    min_sensitive_score: float = 5.0
    max_ticket_bytes: int = 20480
    chunk_words: int = 220
    timeout: float = 30.0
    retries: int = 2

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            model=env("TRIAGEGUARD_MODEL", cls.model),
            min_score=float(env("TRIAGEGUARD_MIN_SCORE", cls.min_score)),
            min_sensitive_score=float(
                env("TRIAGEGUARD_MIN_SENSITIVE_SCORE", cls.min_sensitive_score)
            ),
        )
