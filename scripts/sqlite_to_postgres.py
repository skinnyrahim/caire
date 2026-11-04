"""Copy CAIRE Cover data from the old SQLite file into Postgres (one-time).

Usage, in the Render Shell of the caire-cover service:

    python scripts/sqlite_to_postgres.py /var/data/caire.db           # check counts only
    python scripts/sqlite_to_postgres.py /var/data/caire.db --replace  # copy

DATABASE_URL must point at the Postgres database (Render sets it). --replace
empties the Postgres tables first, including the admin account created on
first start, so the staff accounts and passwords from SQLite carry over.
Everything runs in one transaction: if anything fails, Postgres is unchanged.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import connect, init_schema  # noqa: E402

# Parents before children (foreign keys).
TABLES = ["members", "payments", "claims", "pending_claims", "users", "audit", "counters"]
SERIAL = ["payments", "pending_claims", "users", "audit"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite_path")
    ap.add_argument("--replace", action="store_true", help="empty Postgres tables, then copy")
    a = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set.")
    if not os.path.exists(a.sqlite_path):
        sys.exit(f"{a.sqlite_path} not found.")

    src = sqlite3.connect(a.sqlite_path)
    src.row_factory = sqlite3.Row
    init_schema(src, False)  # brings an old file up to the current columns
    pg = connect(url)
    init_schema(pg, True)

    for t in TABLES:
        n_src = src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        n_pg = pg.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t:15} sqlite {n_src:6}   postgres {n_pg:6}")
    if not a.replace:
        print("\nNothing copied. Run again with --replace to copy.")
        return

    for t in reversed(TABLES):
        pg.execute(f"DELETE FROM {t}")
    for t in TABLES:
        rows = src.execute(f"SELECT * FROM {t}").fetchall()
        if not rows:
            continue
        cols = rows[0].keys()
        sql = f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
        for r in rows:
            pg.execute(sql, tuple(r))
    for t in SERIAL:  # continue numbering after the copied ids
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}),0)+1, false)")
    pg.commit()
    print("\nCopied. New counts:")
    for t in TABLES:
        print(f"{t:15} {pg.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:6}")


if __name__ == "__main__":
    main()
