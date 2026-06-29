"""Land COCO (images) into the raw layer.

Heavy bytes (the pixels) go to RustFS as objects under ``assets/coco/``; the
DuckLake tables hold only URIs + metadata:

  * raw.coco_images       one row per image  (image_id, image_uri, w, h, split)
  * raw.coco_annotations  one row per object (image_id, image_uri, category, bbox)

Source defaults to ``detection-datasets/coco`` (clean object-detection schema:
image + objects{category, bbox}, with 'person' among the class names so the gold
crowded-scene query works). Override with COCO_DATASET / COCO_SPLIT /
COCO_NUM_IMAGES. Pixels never enter a table -- that is what lets DuckDB act as
the query engine for a vision dataset.
"""
from __future__ import annotations

import os

from coco_ingest import arrow_annotations, arrow_images, fetch_and_upload
from common import connect

DATASET = os.environ.get("COCO_DATASET", "detection-datasets/coco")
SPLIT = os.environ.get("COCO_SPLIT", "val")
NUM_IMAGES = int(os.environ.get("COCO_NUM_IMAGES", "200"))


def main() -> int:
    img_rows, ann_rows = fetch_and_upload(DATASET, f"{SPLIT}[:{NUM_IMAGES}]", SPLIT)

    con = connect()
    con.register("coco_img_df", arrow_images(img_rows))
    con.register("coco_ann_df", arrow_annotations(ann_rows))
    con.execute("CREATE OR REPLACE TABLE raw.coco_images AS SELECT * FROM coco_img_df")
    con.execute("CREATE OR REPLACE TABLE raw.coco_annotations AS SELECT * FROM coco_ann_df")

    n_img = con.execute("SELECT count(*) FROM raw.coco_images").fetchone()[0]
    n_ann = con.execute("SELECT count(*) FROM raw.coco_annotations").fetchone()[0]
    print(f"raw.coco_images={n_img} rows, raw.coco_annotations={n_ann} rows")
    try:
        print("latest snapshots:")
        for snap in con.execute(
            "FROM ducklake_snapshots('lake') ORDER BY snapshot_id DESC LIMIT 3"
        ).fetchall():
            print("  ", snap)
    except Exception as exc:  # noqa: BLE001
        print(f"(snapshot listing skipped: {exc})")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
