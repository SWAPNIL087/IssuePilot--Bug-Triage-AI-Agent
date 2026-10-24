"""Create a development SQLite database with sample orders:  python -m triageguard.actions.devdb [path]"""
import sqlite3
import sys
from pathlib import Path

from triageguard.config import ROOT

SCHEMA = ROOT / "mcp" / "schema.sqlite.sql"
SAMPLE_ORDERS = [
    ("ORD-100001", "alice@example.com", "Pending"),
    ("ORD-100002", "alice@example.com", "Invoiced"),
    ("ORD-100003", "bob@example.com", "Pending"),
    ("ORD-100004", "alice@example.com", "Completed"),
    ("ORD-100005", "bob@example.com", "Invoiced"),
]


def create(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.executemany("INSERT INTO orders (order_id, customer_email, status) VALUES (?, ?, ?)", SAMPLE_ORDERS)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ROOT / "mcp" / "dev.db"
    create(target)
    print(f"created {target} with {len(SAMPLE_ORDERS)} sample orders")
