"""Incrementally ingest another slice of COCO into the raw layer.

Demonstrates the local-storage -> lakehouse flow producing a NEW snapshot:
images are uploaded to RustFS and the new metadata rows are INSERTed (not
replaced) into the existing raw tables. Controlled by COCO_INCREMENT_OFFSET
(default = COCO_NUM_IMAGES) and COCO_INCREMENT_COUNT (default 50).
"""
from __future__ import annotations

import os

from coco_ingest import arrow_annotations, arrow_images, fetch_and_upload
from common import connect

DATASET = os.environ.get("COCO_DATASET", "detection-datasets/coco")
SPLIT = os.environ.get("COCO_SPLIT", "val")
NUM_IMAGES = int(os.environ.get("COCO_NUM_IMAGES", "200"))
OFFSET = int(os.environ.get("COCO_INCREMENT_OFFSET", str(NUM_IMAGES)))
COUNT = int(os.environ.get("COCO_INCREMENT_COUNT", "50"))


def table_exists(con, schema: str, name: str) -> bool:
    return (
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
            [schema, name],
        ).fetchone()[0]
        > 0
    )


def main() -> int:
    con = connect()
    if not (table_exists(con, "raw", "coco_images") and table_exists(con, "raw", "coco_annotations")):
        print("raw COCO tables not found; run scripts/10_raw_coco.py first.")
        return 1

    before = con.execute("SELECT count(*) FROM raw.coco_images").fetchone()[0]
    split_expr = f"{SPLIT}[{OFFSET}:{OFFSET + COUNT}]"
    img_rows, ann_rows = fetch_and_upload(DATASET, split_expr, SPLIT)

    con.register("inc_img_df", arrow_images(img_rows))
    con.register("inc_ann_df", arrow_annotations(ann_rows))
    con.execute("INSERT INTO raw.coco_images SELECT * FROM inc_img_df")
    con.execute("INSERT INTO raw.coco_annotations SELECT * FROM inc_ann_df")

    after = con.execute("SELECT count(*) FROM raw.coco_images").fetchone()[0]
    print(f"raw.coco_images {before} -> {after} (+{after - before} images); new snapshot recorded")
    print("latest snapshots:")
    for snap in con.execute("FROM ducklake_snapshots('lake') ORDER BY snapshot_id DESC LIMIT 3").fetchall():
        print("  ", snap)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
