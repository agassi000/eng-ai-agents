"""Demonstrate DuckLake version control on one table:

  * take a sequence of snapshots,
  * time-travel by VERSION and by TIMESTAMP,
  * compare two snapshots,
  * roll back a deliberately bad transform.

Uses a throwaway table gold.demo_labels so real tables are untouched.
"""
from __future__ import annotations

from common import connect


def table_exists(con, fq: str) -> bool:
    schema, name = fq.split(".")
    return (
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
            [schema, name],
        ).fetchone()[0]
        > 0
    )


def snapshots(con) -> list[dict]:
    cur = con.execute("SELECT * FROM ducklake_snapshots('lake')")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def latest_version(con) -> int:
    return con.execute("SELECT max(snapshot_id) FROM ducklake_snapshots('lake')").fetchone()[0]


def time_of(con, version: int):
    snaps = snapshots(con)
    if not snaps:
        return None
    tcol = next((c for c in snaps[0] if "time" in c.lower()), None)
    if tcol is None:
        return None
    for s in snaps:
        if s.get("snapshot_id") == version:
            return s.get(tcol)
    return None


def count_at(con, version: int) -> int:
    return con.execute(f"SELECT count(*) FROM gold.demo_labels AT (VERSION => {version})").fetchone()[0]


def main() -> int:
    con = connect()

    # ---- build the demo table (snapshot: initial) ----------------------------
    if table_exists(con, "gold.coco_train"):
        con.execute("CREATE OR REPLACE TABLE gold.demo_labels AS SELECT image_id, primary_label FROM gold.coco_train")
    elif table_exists(con, "silver.coco_annotations"):
        con.execute(
            "CREATE OR REPLACE TABLE gold.demo_labels AS "
            "SELECT DISTINCT image_id, category AS primary_label FROM silver.coco_annotations"
        )
    else:
        print("Need silver/gold COCO tables first (run silver + gold).")
        return 1

    v_init = latest_version(con)
    print(f"[snapshot] initial build  version={v_init} rows={count_at(con, v_init)}")

    # ---- a legitimate change (snapshot: good) --------------------------------
    con.execute("INSERT INTO gold.demo_labels VALUES (-1, 'sentinel')")
    v_good = latest_version(con)
    n_good = count_at(con, v_good)
    t_good = time_of(con, v_good)
    print(f"[snapshot] after insert    version={v_good} rows={n_good} time={t_good}")

    # ---- a DELIBERATELY BAD transform (snapshot: bad) ------------------------
    con.execute("DELETE FROM gold.demo_labels")
    v_bad = latest_version(con)
    print(f"[snapshot] bad transform   version={v_bad} rows={count_at(con, v_bad)}  <-- data wiped!")

    # ---- time travel by VERSION ----------------------------------------------
    print("\n-- time travel by VERSION --")
    print(f"  rows AT good v{v_good} = {count_at(con, v_good)}")
    print(f"  rows AT bad  v{v_bad} = {count_at(con, v_bad)}")

    # ---- time travel by TIMESTAMP --------------------------------------------
    if t_good is not None:
        print("\n-- time travel by TIMESTAMP --")
        try:
            n = con.execute(
                f"SELECT count(*) FROM gold.demo_labels AT (TIMESTAMP => TIMESTAMP '{t_good}')"
            ).fetchone()[0]
            print(f"  rows AT timestamp {t_good} = {n}")
        except Exception as exc:  # noqa: BLE001
            print(f"  (timestamp travel syntax differs in this version: {exc})")

    # ---- compare two snapshots -----------------------------------------------
    print("\n-- compare snapshots (good vs bad) --")
    only_in_good = con.execute(
        f"""
        SELECT count(*) FROM (
            SELECT * FROM gold.demo_labels AT (VERSION => {v_good})
            EXCEPT
            SELECT * FROM gold.demo_labels AT (VERSION => {v_bad})
        )
        """
    ).fetchone()[0]
    print(f"  rows present in v{v_good} but missing in v{v_bad}: {only_in_good}")

    # ---- rollback the bad transform ------------------------------------------
    con.execute(f"CREATE OR REPLACE TABLE gold.demo_labels AS SELECT * FROM gold.demo_labels AT (VERSION => {v_good})")
    v_rollback = latest_version(con)
    print(f"\n[snapshot] rollback        version={v_rollback} rows={count_at(con, v_rollback)} (restored to v{v_good})")

    print("\n-- full snapshot history --")
    for s in snapshots(con):
        print("  ", s)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
