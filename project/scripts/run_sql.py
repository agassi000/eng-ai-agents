"""Run one or more .sql files against the attached DuckLake catalog.

Usage (inside the lab container):
    python scripts/run_sql.py sql/20_silver.sql sql/30_gold.sql

Statements that return rows (SELECT/WITH/CALL/...) have their output printed,
so the demonstration queries in 30_gold.sql show their results.
"""
from __future__ import annotations

import sys

from common import connect, run_sql_file


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python scripts/run_sql.py <file.sql> [more.sql ...]")
        return 2
    con = connect()
    try:
        for path in argv[1:]:
            print(f"\n===== running {path} =====")
            run_sql_file(con, path)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
