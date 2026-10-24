"""Check the database user is least-privilege:  python -m triageguard.actions.permcheck  (uses TRIAGEGUARD_ACTIONS_DB)
Every statement below must be REFUSED. Exit code 1 if any is allowed."""
import sys

from triageguard.actions.db import connect
from triageguard.config import Config, load_env

FORBIDDEN = {
    "DELETE rows": "DELETE FROM orders WHERE 1 = 0",
    "update another column": "UPDATE orders SET customer_email = customer_email WHERE 1 = 0",
    "rewrite the audit log": "UPDATE agent_audit SET outcome = outcome WHERE 1 = 0",
    "delete from the audit log": "DELETE FROM agent_audit WHERE 1 = 0",
    "create a table": "CREATE TABLE permcheck_probe (a INT)",
    "drop a table": "DROP TABLE order_status_history",
}


def main() -> int:
    load_env()
    url = Config.from_env().actions_db
    if not url:
        print("error: set TRIAGEGUARD_ACTIONS_DB", file=sys.stderr)
        return 2
    failures = 0
    for name, sql in FORBIDDEN.items():
        conn, _, explicit = connect(url)
        try:
            conn.cursor().execute(sql)
            print(f"FAIL  {name} was allowed")
            failures += 1
        except Exception as e:
            print(f"ok    {name} refused ({str(e).splitlines()[0][:70]})")
        finally:
            try:
                conn.rollback() if not explicit else None
            finally:
                conn.close()
    print("least-privilege check " + ("FAILED" if failures else "passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())