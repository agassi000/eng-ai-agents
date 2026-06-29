"""Shared configuration and helpers for the lakehouse pipeline.

Every script connects to DuckDB through :func:`connect`, which runs
``sql/00_attach.sql`` (extensions + S3 secret + ATTACH DuckLake). S3/RustFS
access for object-level work (bucket admin, blob uploads, raw-object listing)
goes through :func:`s3_client`.

All configuration comes from environment variables that docker-compose injects
into the ``lab`` container, with sensible local defaults so the module also
imports cleanly on the host for syntax checks.
"""
from __future__ import annotations

import os
import pathlib
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SQL_DIR = REPO_ROOT / "sql"

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://rustfs:9000")
S3_BUCKET = os.environ.get("S3_BUCKET", "lakehouse")
S3_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "rustfsadmin")
S3_SECRET = os.environ.get("AWS_SECRET_ACCESS_KEY", "rustfsadmin")


def s3_uri(*parts: str) -> str:
    """Build an ``s3://bucket/...`` URI from path parts."""
    cleaned = [str(p).strip("/") for p in parts if str(p) != ""]
    return "s3://" + "/".join([S3_BUCKET, *cleaned])


def s3_client():
    """Return a boto3 S3 client pointed at RustFS.

    boto3 does not read S3_ENDPOINT automatically; we pass endpoint_url here.
    """
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET,
        region_name="us-east-1",
    )


def wait_for_s3(timeout: float = 90.0, interval: float = 2.0):
    """Block until RustFS answers an S3 request, then return the client."""
    client = s3_client()
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            client.list_buckets()
            return client
        except Exception as exc:  # noqa: BLE001 - we re-raise after the deadline
            last_err = exc
            time.sleep(interval)
    raise TimeoutError(f"RustFS not reachable at {S3_ENDPOINT}: {last_err}")


def connect(attach_sql: str = "00_attach.sql"):
    """Open a DuckDB connection with DuckLake attached (runs the attach script)."""
    import duckdb

    con = duckdb.connect()
    con.execute((SQL_DIR / attach_sql).read_text())
    return con


def split_sql(script: str) -> list[str]:
    """Split a SQL script into individual statements.

    Respects single-quoted string literals and ``--`` line comments so that
    semicolons inside strings/comments do not break statements. Adequate for the
    controlled SQL in this repo (no block comments, no dollar-quoting).
    """
    statements: list[str] = []
    buf: list[str] = []
    in_string = False
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if in_string:
            buf.append(ch)
            if ch == "'":
                # handle escaped '' inside a string literal
                if i + 1 < n and script[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and script[i + 1] == "-":
            # skip to end of line
            while i < n and script[i] != "\n":
                i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def run_sql_file(con, path: str, echo: bool = True) -> None:
    """Execute every statement in a .sql file, printing rows for queries."""
    script = pathlib.Path(path).read_text()
    for stmt in split_sql(script):
        head = stmt.lstrip().split(None, 1)[0].upper() if stmt.strip() else ""
        is_query = head in {"SELECT", "WITH", "FROM", "PRAGMA", "SHOW", "DESCRIBE", "CALL"}
        try:
            if is_query:
                rows = con.execute(stmt).fetchall()
                if echo:
                    cols = [d[0] for d in con.description] if con.description else []
                    print(f"\n-- {stmt.splitlines()[0][:80]} ...")
                    if cols:
                        print(" | ".join(cols))
                    for r in rows[:50]:
                        print(" | ".join("" if v is None else str(v) for v in r))
                    if len(rows) > 50:
                        print(f"... ({len(rows)} rows total)")
            else:
                con.execute(stmt)
                if echo:
                    print(f"ok: {stmt.splitlines()[0][:80]}")
        except Exception as exc:  # noqa: BLE001 - keep going so optional paths don't abort the run
            print(f"ERROR (continuing): {stmt.splitlines()[0][:80]}\n        {exc}")
